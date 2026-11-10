"""
MediFlow - Testes da segurança da API: chave de acesso, limite de 15 MB e tetos diários
Hackathon ONE G10 - Grupo 10: MediFlow

Mapeamento: tests/test_seguranca.py
A segurança é um middleware ASGI (api/seguranca.py) que roda antes de qualquer rota. Os testes usam a
aplicação de verdade com um armazenamento local temporário, e chamam o middleware direto quando
precisam provar que o corpo da requisição não foi lido.
Execução (a partir da raiz): python -m pytest tests/test_seguranca.py -v
"""

import asyncio
import base64
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import api.seguranca as seguranca
import api.triagem as modulo_triagem
from api.main import app, obter_repositorio
from api.seguranca import CONTADOR_DIARIO, ConfiguracaoInvalida, ContadorDiario, SegurancaMiddleware, ler_configuracao
from armazenamento.repositorio import RepositorioLocal

PASTA_DADOS = Path(__file__).resolve().parent.parent / "dados_teste"
MENSAGEM_401 = {"detail": "Chave de acesso ausente ou inválida."}
COMANDO_DA_CHAVE = 'python -c "import secrets; print(secrets.token_urlsafe(32))"'


@pytest.fixture
def repositorio(tmp_path):
    return RepositorioLocal(tmp_path)


@pytest.fixture(autouse=True)
def armazenamento_de_teste(repositorio):
    app.dependency_overrides[obter_repositorio] = lambda: repositorio
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def sem_chave():
    """Um cliente que NÃO manda a chave de acesso."""
    return TestClient(app)


def entrada_de_triagem(documento_id="DOC-SEG-1"):
    cenario = json.loads((PASTA_DADOS / "1_rotina" / "exemplo.json").read_text(encoding="utf-8"))
    return dict(cenario["requisicao_entrada"], documento_id=documento_id)


def nada_gravado(tmp_path):
    return list(tmp_path.rglob("*.json")) == []


# ============================================================================
# Chave de acesso
# ============================================================================

def test_saude_responde_200_sem_chave(sem_chave):
    resposta = sem_chave.get("/api/v1/saude")

    assert resposta.status_code == 200
    assert resposta.json() == {"status": "ok", "armazenamento": "local"}


def test_saude_responde_200_mesmo_com_chave_errada(sem_chave):
    assert sem_chave.get("/api/v1/saude", headers={"X-API-Key": "errada"}).status_code == 200


def test_a_excecao_da_saude_vale_so_para_o_get(sem_chave):
    # POST na mesma rota não é o healthcheck: continua exigindo a chave.
    assert sem_chave.post("/api/v1/saude").status_code == 401


ROTAS_PROTEGIDAS = [
    ("POST", "/api/v1/triagem"),
    ("GET", "/api/v1/auditoria/pendentes"),
    ("POST", "/api/v1/auditoria/DOC-1/decisao"),
    ("GET", "/api/v1/documentos"),
    ("GET", "/docs"),
    ("GET", "/redoc"),
    ("GET", "/openapi.json"),
    ("GET", "/rota-que-nao-existe"),  # uma rota nova (ou inexistente) nasce protegida
    ("DELETE", "/api/v1/documentos"),
]


@pytest.mark.parametrize("metodo, caminho", ROTAS_PROTEGIDAS)
def test_sem_chave_da_401_e_nada_e_gravado(sem_chave, tmp_path, metodo, caminho):
    resposta = sem_chave.request(metodo, caminho, json=entrada_de_triagem())

    assert resposta.status_code == 401
    assert resposta.json() == MENSAGEM_401
    assert nada_gravado(tmp_path)


@pytest.mark.parametrize(
    "chave",
    [
        "",
        "errada",
        "chave-de-teste-" + "x" * 39,  # um caractere a menos
        "chave-de-teste-" + "x" * 41,  # um caractere a mais
        "CHAVE-DE-TESTE-" + "X" * 40,  # outra caixa
        "chave-de-teste-" + "y" * 40,  # mesmo tamanho, outro conteúdo
    ],
)
def test_chave_errada_da_401_e_nada_e_gravado(sem_chave, tmp_path, chave):
    resposta = sem_chave.post("/api/v1/triagem", json=entrada_de_triagem(), headers={"X-API-Key": chave})

    assert resposta.status_code == 401
    assert resposta.json() == MENSAGEM_401
    assert nada_gravado(tmp_path)


def test_chave_certa_responde_200_na_triagem_e_nas_listas(novo_cliente, tmp_path):
    cliente = novo_cliente()

    assert cliente.post("/api/v1/triagem", json=entrada_de_triagem()).status_code == 200
    assert cliente.get("/api/v1/documentos").status_code == 200
    assert cliente.get("/api/v1/auditoria/pendentes").status_code == 200
    assert not nada_gravado(tmp_path)


def test_o_nome_do_cabecalho_nao_diferencia_maiusculas(sem_chave, chave_de_teste):
    for nome in ("x-api-key", "X-API-KEY", "X-Api-Key"):
        assert sem_chave.get("/api/v1/documentos", headers={nome: chave_de_teste}).status_code == 200


def test_a_chave_no_corpo_ou_na_url_nao_vale(sem_chave, chave_de_teste):
    com_chave_no_corpo = dict(entrada_de_triagem(), chave=chave_de_teste, **{"X-API-Key": chave_de_teste})

    assert sem_chave.post("/api/v1/triagem", json=com_chave_no_corpo).status_code == 401
    assert sem_chave.get(f"/api/v1/documentos?x-api-key={chave_de_teste}").status_code == 401


def test_a_chave_e_comparada_com_compare_digest(sem_chave, chave_de_teste, monkeypatch):
    chamadas = []
    original = seguranca.hmac.compare_digest

    def espiao(a, b):
        chamadas.append((a, b))
        return original(a, b)

    monkeypatch.setattr(seguranca.hmac, "compare_digest", espiao)

    sem_chave.get("/api/v1/documentos", headers={"X-API-Key": chave_de_teste})
    sem_chave.get("/api/v1/documentos", headers={"X-API-Key": "errada"})

    assert chamadas == [
        (chave_de_teste.encode(), chave_de_teste.encode()),
        (b"errada", chave_de_teste.encode()),
    ]


def test_sem_a_chave_certa_o_corpo_da_requisicao_nem_e_lido():
    chamadas = []

    async def aplicacao_que_nao_pode_ser_chamada(scope, receive, send):
        chamadas.append("aplicacao")

    async def receive():
        chamadas.append("receive")
        return {"type": "http.request", "body": b"x" * 1000, "more_body": False}

    respostas = []

    async def send(mensagem):
        respostas.append(mensagem)

    escopo = {"type": "http", "method": "POST", "path": "/api/v1/triagem", "headers": [(b"content-length", b"1000")]}

    asyncio.run(SegurancaMiddleware(aplicacao_que_nao_pode_ser_chamada)(escopo, receive, send))

    assert chamadas == []  # nem a rota, nem a leitura do corpo
    assert respostas[0]["status"] == 401


def test_a_chave_nunca_aparece_em_log_nem_em_resposta(sem_chave, chave_de_teste, caplog):
    tentativa_errada = "tentativa-errada-" + "z" * 30
    with caplog.at_level(logging.DEBUG):
        respostas = [
            sem_chave.get("/api/v1/documentos", headers={"X-API-Key": chave_de_teste}),
            sem_chave.get("/api/v1/documentos", headers={"X-API-Key": tentativa_errada}),
            sem_chave.get("/api/v1/documentos"),
            sem_chave.get("/api/v1/saude"),
        ]

    for resposta in respostas:
        assert chave_de_teste not in resposta.text
        assert chave_de_teste not in str(dict(resposta.headers))
        assert tentativa_errada not in resposta.text
    assert chave_de_teste not in caplog.text
    assert tentativa_errada not in caplog.text


def test_a_configuracao_nao_mostra_a_chave_no_repr(chave_de_teste):
    assert chave_de_teste not in repr(ler_configuracao())


# ============================================================================
# Configuração: a API não sobe sem chave
# ============================================================================

def test_sem_chave_a_api_nao_sobe_e_a_mensagem_diz_como_gerar(monkeypatch):
    monkeypatch.delenv("MEDIFLOW_API_KEY")

    with pytest.raises(ConfiguracaoInvalida) as capturada:
        with TestClient(app):
            pass

    assert "MEDIFLOW_API_KEY não está definida" in str(capturada.value)
    assert COMANDO_DA_CHAVE in str(capturada.value)


@pytest.mark.parametrize("valor", ["", "   "])
def test_chave_vazia_como_no_env_example_tambem_nao_sobe(monkeypatch, valor):
    monkeypatch.setenv("MEDIFLOW_API_KEY", valor)

    with pytest.raises(ConfiguracaoInvalida, match="não está definida"):
        with TestClient(app):
            pass


def test_chave_curta_nao_sobe_e_a_mensagem_nao_traz_a_chave(monkeypatch):
    curta = "k" * 31
    monkeypatch.setenv("MEDIFLOW_API_KEY", curta)

    with pytest.raises(ConfiguracaoInvalida) as capturada:
        with TestClient(app):
            pass

    assert "31 caracteres" in str(capturada.value) and "32" in str(capturada.value)
    assert COMANDO_DA_CHAVE in str(capturada.value)
    assert curta not in str(capturada.value)


def test_chave_de_32_caracteres_sobe(monkeypatch):
    monkeypatch.setenv("MEDIFLOW_API_KEY", "k" * 32)

    with TestClient(app) as cliente:
        assert cliente.get("/api/v1/saude").status_code == 200
        assert cliente.get("/api/v1/documentos", headers={"X-API-Key": "k" * 32}).status_code == 200


def test_os_espacos_em_volta_da_chave_do_ambiente_sao_ignorados(monkeypatch):
    monkeypatch.setenv("MEDIFLOW_API_KEY", "  " + "k" * 32 + "  ")

    assert ler_configuracao().chave == "k" * 32


@pytest.mark.parametrize("valor", ["desligada", "DESLIGADA", "  Desligada  "])
def test_com_a_autenticacao_desligada_a_api_sobe_sem_chave_e_registra_um_aviso(monkeypatch, caplog, valor):
    monkeypatch.delenv("MEDIFLOW_API_KEY")
    monkeypatch.setenv("MEDIFLOW_API_AUTH", valor)

    with caplog.at_level(logging.WARNING, logger="mediflow.api"):
        with TestClient(app) as cliente:
            resposta = cliente.get("/api/v1/documentos")  # sem a chave

    assert resposta.status_code == 200
    avisos = [r.getMessage() for r in caplog.records if r.name == "mediflow.api" and r.levelno == logging.WARNING]
    assert len(avisos) == 1 and "SEM chave de acesso" in avisos[0]


def test_com_a_autenticacao_desligada_a_triagem_funciona_sem_chave(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIFLOW_API_AUTH", "desligada")

    resposta = TestClient(app).post("/api/v1/triagem", json=entrada_de_triagem())

    assert resposta.status_code == 200
    assert not nada_gravado(tmp_path)


@pytest.mark.parametrize("valor", ["off", "false", "nao", "desligado", "ligada", ""])
def test_so_o_valor_desligada_desliga_a_autenticacao(monkeypatch, sem_chave, valor):
    # Qualquer outro valor, inclusive um erro de digitação, deixa a proteção ligada.
    monkeypatch.setenv("MEDIFLOW_API_AUTH", valor)

    assert sem_chave.get("/api/v1/documentos").status_code == 401


def test_com_a_chave_a_api_nao_avisa_de_autenticacao_desligada(caplog):
    with caplog.at_level(logging.WARNING, logger="mediflow.api"):
        with TestClient(app):
            pass

    assert [r for r in caplog.records if r.name == "mediflow.api"] == []


# ============================================================================
# Configuração inválida em tempo de requisição (teste sem lifespan): falha fechada
# ============================================================================

@pytest.mark.parametrize("chave", [None, "", "curta"])
@pytest.mark.parametrize("metodo, caminho", [("GET", "/api/v1/saude"), ("POST", "/api/v1/triagem"), ("GET", "/api/v1/documentos")])
def test_configuracao_invalida_na_requisicao_da_503_e_nada_passa(monkeypatch, tmp_path, chave_de_teste, chave, metodo, caminho):
    if chave is None:
        monkeypatch.delenv("MEDIFLOW_API_KEY")
    else:
        monkeypatch.setenv("MEDIFLOW_API_KEY", chave)

    # Nem com o cabeçalho certo (o que o cliente tinha antes) nem no healthcheck: tudo é recusado.
    resposta = TestClient(app).request(metodo, caminho, json=entrada_de_triagem(), headers={"X-API-Key": chave_de_teste})

    assert resposta.status_code == 503
    assert resposta.json() == {"detail": "Configuração de segurança da API inválida."}
    assert nada_gravado(tmp_path)
    assert chave_de_teste not in resposta.text


def test_configuracao_invalida_registra_um_erro_sem_a_chave(monkeypatch, caplog):
    monkeypatch.setenv("MEDIFLOW_API_KEY", "curta-demais-SEGREDO")

    with caplog.at_level(logging.DEBUG):
        TestClient(app).get("/api/v1/documentos")

    erros = [r.getMessage() for r in caplog.records if r.name == "mediflow.api.seguranca" and r.levelno == logging.ERROR]
    assert len(erros) == 1 and "503" in erros[0]
    assert "SEGREDO" not in caplog.text


# ============================================================================
# WebSocket: a API não tem rotas de WebSocket e todas ficam fechadas
# ============================================================================

def test_conexao_websocket_e_recusada(sem_chave, chave_de_teste):
    for cabecalhos in ({}, {"X-API-Key": chave_de_teste}):
        with pytest.raises(WebSocketDisconnect) as capturada:
            with sem_chave.websocket_connect("/ws", headers=cabecalhos):
                pass
        assert capturada.value.code == 1008


# ============================================================================
# Limite de 15 MB (o mesmo client_max_body_size 15M do nginx)
# ============================================================================

UM_MIB = 1024 * 1024
LIMITE = 15 * UM_MIB


class ResultadoDaChamada:
    def __init__(self):
        self.status = None  # o status que o middleware respondeu (None se não respondeu)
        self.corpo_da_resposta = None
        self.leituras = 0  # quantas vezes o middleware leu o corpo do cliente
        self.app_chamada = False
        self.mensagens_da_app = []  # o que a rota recebeu ao ler o corpo


def chamar_middleware(chave, pedacos, content_length=None, com_chave=True, desconecta_no_fim=False):
    """Chama o middleware direto, com o corpo em pedaços, como um servidor ASGI faria."""
    resultado = ResultadoDaChamada()
    fila = [{"type": "http.request", "body": p, "more_body": i < len(pedacos) - 1} for i, p in enumerate(pedacos)]
    if desconecta_no_fim:
        fila = fila[:-1] + [{"type": "http.disconnect"}]

    async def receive():
        resultado.leituras += 1
        return fila.pop(0) if fila else {"type": "http.disconnect"}

    async def send(mensagem):
        if mensagem["type"] == "http.response.start":
            resultado.status = mensagem["status"]
        elif mensagem["type"] == "http.response.body":
            resultado.corpo_da_resposta = (resultado.corpo_da_resposta or b"") + mensagem.get("body", b"")

    async def aplicacao_de_destino(scope, receive_da_rota, send_da_rota):
        resultado.app_chamada = True
        resultado.mensagens_da_app.append(await receive_da_rota())
        resultado.mensagens_da_app.append(await receive_da_rota())
        await send_da_rota({"type": "http.response.start", "status": 200, "headers": []})
        await send_da_rota({"type": "http.response.body", "body": b""})

    cabecalhos = [(b"x-api-key", chave.encode())] if com_chave else []
    if content_length is not None:
        cabecalhos.append((b"content-length", str(content_length).encode()))
    escopo = {"type": "http", "method": "POST", "path": "/api/v1/triagem", "headers": cabecalhos}

    asyncio.run(SegurancaMiddleware(aplicacao_de_destino)(escopo, receive, send))
    return resultado


def test_content_length_acima_do_limite_da_413_antes_de_ler_o_corpo(chave_de_teste):
    resultado = chamar_middleware(chave_de_teste, [b"x"], content_length=LIMITE + 1)

    assert resultado.status == 413
    assert resultado.leituras == 0  # nem uma leitura do corpo
    assert not resultado.app_chamada
    assert json.loads(resultado.corpo_da_resposta) == {"detail": "Corpo da requisição acima do limite de 15 MB."}


def test_sem_content_length_o_corpo_e_lido_com_teto_e_cortado_em_413(chave_de_teste):
    pedacos = [b"x" * UM_MIB] * 20  # 20 MiB, sem declarar o tamanho

    resultado = chamar_middleware(chave_de_teste, pedacos)

    assert resultado.status == 413
    assert not resultado.app_chamada
    assert resultado.leituras == 16  # parou ao passar de 15 MiB, sem ler os outros 4 pedaços


def test_content_length_que_mente_tambem_e_cortado_durante_a_leitura(chave_de_teste):
    resultado = chamar_middleware(chave_de_teste, [b"x" * UM_MIB] * 20, content_length=10)

    assert resultado.status == 413
    assert not resultado.app_chamada


def test_exatamente_15_mib_passa_para_a_rota(chave_de_teste):
    resultado = chamar_middleware(chave_de_teste, [b"x" * UM_MIB] * 15, content_length=LIMITE)

    assert resultado.status == 200
    assert resultado.app_chamada
    assert len(resultado.mensagens_da_app[0]["body"]) == LIMITE


def test_um_byte_acima_de_15_mib_da_413(chave_de_teste):
    resultado = chamar_middleware(chave_de_teste, [b"x" * LIMITE, b"y"])

    assert resultado.status == 413


def test_content_length_invalido_vale_como_ausente_e_o_corpo_e_lido_com_teto(chave_de_teste):
    resultado = chamar_middleware(chave_de_teste, [b"abc"], content_length="isso-nao-e-um-numero")

    assert resultado.status == 200
    assert resultado.mensagens_da_app[0]["body"] == b"abc"


def test_o_corpo_lido_em_pedacos_e_repassado_a_rota_inteiro_e_em_memoria(chave_de_teste):
    resultado = chamar_middleware(chave_de_teste, [b"ab", b"cd", b"ef"])

    primeira, segunda = resultado.mensagens_da_app
    assert primeira == {"type": "http.request", "body": b"abcdef", "more_body": False}
    # Depois do corpo, a rota volta ao canal original, que só traz o aviso de desconexão.
    assert segunda == {"type": "http.disconnect"}


def test_corpo_vazio_chega_vazio_a_rota(chave_de_teste):
    resultado = chamar_middleware(chave_de_teste, [b""])

    assert resultado.mensagens_da_app[0] == {"type": "http.request", "body": b"", "more_body": False}


def test_cliente_que_desconecta_no_meio_nao_chama_a_rota_nem_responde(chave_de_teste):
    resultado = chamar_middleware(chave_de_teste, [b"abc", b"def"], desconecta_no_fim=True)

    assert not resultado.app_chamada
    assert resultado.status is None


def test_sem_a_chave_o_401_vem_antes_do_413_e_o_corpo_nao_e_lido(chave_de_teste):
    resultado = chamar_middleware(chave_de_teste, [b"x"], content_length=LIMITE * 2, com_chave=False)

    assert resultado.status == 401
    assert resultado.leituras == 0


def test_corpo_de_16_mb_na_api_de_verdade_da_413_e_nada_e_gravado(novo_cliente, tmp_path):
    resposta = novo_cliente().post(
        "/api/v1/triagem", content=b"x" * (16 * UM_MIB), headers={"content-type": "application/json"}
    )

    assert resposta.status_code == 413
    assert resposta.json() == {"detail": "Corpo da requisição acima do limite de 15 MB."}
    assert nada_gravado(tmp_path)


def test_corpo_de_exatamente_15_mib_chega_a_rota_que_o_recusa_por_ser_invalido(novo_cliente, tmp_path):
    resposta = novo_cliente().post("/api/v1/triagem", content=b"x" * LIMITE, headers={"content-type": "application/json"})

    assert resposta.status_code == 422  # passou pelo middleware; quem recusou foi a validação da rota
    assert nada_gravado(tmp_path)


def test_corpo_grande_e_valido_chega_inteiro_a_rota(novo_cliente, tmp_path):
    texto = ("LAUDO DE EXAME LABORATORIAL. Paciente: Maria Exemplo, 40 anos. Hemograma normal. " * 40_000).encode()
    base64_do_arquivo = base64.b64encode(texto).decode("ascii")
    assert 2 * UM_MIB < len(base64_do_arquivo) < LIMITE  # grande, e ainda dentro do limite

    resposta = novo_cliente().post(
        "/api/v1/triagem",
        json={"documento_id": "DOC-GRANDE", "tipo_arquivo": "TXT", "documento_base64": base64_do_arquivo, "canal_origem": "Ambulatorio"},
    )

    assert resposta.status_code == 200, resposta.text
    gravado = json.loads((tmp_path / "mediflow-recebidos" / "DOC-GRANDE.json").read_text(encoding="utf-8"))
    assert gravado["documento_base64"] == base64_do_arquivo  # o corpo inteiro chegou à rota


# ============================================================================
# Tetos diários: documentos e MB por dia
# ============================================================================

MSG_429_DOCUMENTOS = {"detail": "Limite diário de documentos atingido. Tente novamente amanhã."}
MSG_429_MB = {"detail": "Limite diário de MB atingido. Tente novamente amanhã."}


@pytest.fixture
def chamadas_do_grafo(monkeypatch):
    """Conta quantas vezes a triagem chegou ao grafo (e, portanto, ao Gemini), sem mudar o resultado."""
    chamadas = []
    original = modulo_triagem.executar_triagem_mediflow

    def espiao(entrada, *args, **kwargs):
        chamadas.append(entrada.documento_id)
        return original(entrada, *args, **kwargs)

    monkeypatch.setattr(modulo_triagem, "executar_triagem_mediflow", espiao)
    return chamadas


def enviar_triagem(cliente, numero):
    return cliente.post("/api/v1/triagem", json=entrada_de_triagem(f"DOC-TETO-{numero}"))


def test_teto_de_documentos_o_terceiro_envio_da_429_sem_gravar_e_sem_chamar_o_grafo(monkeypatch, novo_cliente, tmp_path, chamadas_do_grafo):
    monkeypatch.setenv("MEDIFLOW_TETO_DIARIO_DOCUMENTOS", "2")
    cliente = novo_cliente()

    codigos = [enviar_triagem(cliente, n).status_code for n in (1, 2)]
    terceiro = enviar_triagem(cliente, 3)

    assert codigos == [200, 200]
    assert terceiro.status_code == 429
    assert terceiro.json() == MSG_429_DOCUMENTOS
    assert chamadas_do_grafo == ["DOC-TETO-1", "DOC-TETO-2"]  # o terceiro nunca chegou ao grafo
    assert len(list(tmp_path.rglob("*.json"))) == 4  # recebido + destino, só dos dois primeiros
    assert not (tmp_path / "mediflow-recebidos" / "DOC-TETO-3.json").exists()


def test_teto_de_mb_corta_o_envio_que_passaria_do_volume_do_dia(monkeypatch, novo_cliente, tmp_path, chamadas_do_grafo):
    cliente = novo_cliente()
    tamanho = len(cliente.build_request("POST", "/api/v1/triagem", json=entrada_de_triagem("DOC-TETO-1")).content)
    # Cabem dois corpos desse tamanho, e o terceiro passaria do teto.
    monkeypatch.setenv("MEDIFLOW_TETO_DIARIO_MB", str((2 * tamanho + 10) / (1024 * 1024)))

    codigos = [enviar_triagem(cliente, n).status_code for n in (1, 2)]
    terceiro = enviar_triagem(cliente, 3)

    assert codigos == [200, 200]
    assert terceiro.status_code == 429
    assert terceiro.json() == MSG_429_MB
    assert chamadas_do_grafo == ["DOC-TETO-1", "DOC-TETO-2"]
    assert not (tmp_path / "mediflow-recebidos" / "DOC-TETO-3.json").exists()


def test_o_teto_nunca_e_ultrapassado_um_corpo_maior_que_o_que_resta_e_recusado_e_um_menor_ainda_cabe(monkeypatch, novo_cliente):
    cliente = novo_cliente()
    grande = entrada_de_triagem("DOC-GRANDE")
    pequeno = {"documento_id": "DOC-PEQUENO", "documento_texto": "Aviso interno curto.", "canal_origem": "Ambulatorio"}
    tamanho_grande = len(cliente.build_request("POST", "/api/v1/triagem", json=grande).content)
    tamanho_pequeno = len(cliente.build_request("POST", "/api/v1/triagem", json=pequeno).content)
    # Resta espaço para o pequeno depois do grande, mas não para dois grandes.
    monkeypatch.setenv("MEDIFLOW_TETO_DIARIO_MB", str((tamanho_grande + tamanho_pequeno + 5) / (1024 * 1024)))

    assert cliente.post("/api/v1/triagem", json=grande).status_code == 200
    assert cliente.post("/api/v1/triagem", json=dict(grande, documento_id="DOC-GRANDE-2")).status_code == 429
    assert cliente.post("/api/v1/triagem", json=pequeno).status_code == 200  # o 429 não gastou nada do teto


def test_so_a_triagem_conta_para_os_tetos(monkeypatch, novo_cliente, sem_chave):
    monkeypatch.setenv("MEDIFLOW_TETO_DIARIO_DOCUMENTOS", "1")
    cliente = novo_cliente()

    for _ in range(3):
        assert cliente.get("/api/v1/documentos").status_code == 200
        assert cliente.get("/api/v1/auditoria/pendentes").status_code == 200
        assert sem_chave.get("/api/v1/saude").status_code == 200
        assert cliente.post("/api/v1/auditoria/DOC-NAO-EXISTE/decisao", json={"destino": "rotina", "revisor": "Ana", "justificativa": "Conferido."}).status_code == 404

    assert enviar_triagem(cliente, 1).status_code == 200
    assert enviar_triagem(cliente, 2).status_code == 429


def test_requisicao_recusada_pela_chave_ou_pelo_tamanho_nao_gasta_o_teto(monkeypatch, novo_cliente, sem_chave):
    monkeypatch.setenv("MEDIFLOW_TETO_DIARIO_DOCUMENTOS", "1")
    cliente = novo_cliente()

    assert sem_chave.post("/api/v1/triagem", json=entrada_de_triagem("DOC-SEM-CHAVE")).status_code == 401
    assert sem_chave.post("/api/v1/triagem", json=entrada_de_triagem("DOC-CHAVE-ERRADA"), headers={"X-API-Key": "errada"}).status_code == 401
    assert cliente.post("/api/v1/triagem", content=b"x" * (16 * UM_MIB), headers={"content-type": "application/json"}).status_code == 413

    assert enviar_triagem(cliente, 1).status_code == 200  # o teto de 1 ainda estava inteiro


def test_requisicao_invalida_que_passou_pela_chave_e_pelo_tamanho_conta(monkeypatch, novo_cliente):
    monkeypatch.setenv("MEDIFLOW_TETO_DIARIO_DOCUMENTOS", "1")
    cliente = novo_cliente()

    invalida = cliente.post("/api/v1/triagem", json={"documento_id": "DOC-INVALIDO"})  # sem texto nem Base64

    assert invalida.status_code == 422
    assert enviar_triagem(cliente, 1).status_code == 429


def test_sem_a_chave_o_401_vem_antes_do_429(monkeypatch, novo_cliente, sem_chave):
    monkeypatch.setenv("MEDIFLOW_TETO_DIARIO_DOCUMENTOS", "1")
    assert enviar_triagem(novo_cliente(), 1).status_code == 200

    assert sem_chave.post("/api/v1/triagem", json=entrada_de_triagem("DOC-TETO-2")).status_code == 401


def test_com_a_autenticacao_desligada_os_tetos_continuam_valendo(monkeypatch, novo_cliente):
    monkeypatch.setenv("MEDIFLOW_API_AUTH", "desligada")
    monkeypatch.setenv("MEDIFLOW_TETO_DIARIO_DOCUMENTOS", "1")
    cliente = TestClient(app)

    assert enviar_triagem(cliente, 1).status_code == 200
    assert enviar_triagem(cliente, 2).status_code == 429


# --- A virada do dia, com um relógio injetável (sem sleep) -------------------------------------

def relogio_em(momento):
    """Um relógio que devolve `momento` e que o teste pode mudar."""
    estado = {"agora": momento}
    return estado, lambda: estado["agora"]


def utc(*partes):
    return datetime(*partes, tzinfo=timezone.utc)


def test_a_virada_do_dia_zera_o_contador_a_meia_noite_de_sao_paulo():
    # Em novembro São Paulo está em UTC-3: meia-noite lá é 03:00 em UTC.
    estado, relogio = relogio_em(utc(2026, 11, 10, 15, 0))
    contador = ContadorDiario(relogio)

    assert contador.tentar_registrar(100, 2, 10_000) is None
    assert contador.tentar_registrar(100, 2, 10_000) is None
    assert contador.tentar_registrar(100, 2, 10_000) == "documentos"

    estado["agora"] = utc(2026, 11, 11, 2, 59, 59)  # 23:59:59 em São Paulo: ainda é o mesmo dia
    assert contador.tentar_registrar(100, 2, 10_000) == "documentos"

    estado["agora"] = utc(2026, 11, 11, 3, 0, 0)  # 00:00:00 em São Paulo: dia novo, contador zerado
    assert contador.tentar_registrar(100, 2, 10_000) is None
    assert contador.tentar_registrar(100, 2, 10_000) is None
    assert contador.tentar_registrar(100, 2, 10_000) == "documentos"


def test_a_virada_do_dia_zera_tambem_o_volume_em_mb():
    estado, relogio = relogio_em(utc(2026, 11, 10, 15, 0))
    contador = ContadorDiario(relogio)

    assert contador.tentar_registrar(900, 100, 1000) is None
    assert contador.tentar_registrar(200, 100, 1000) == "mb"

    estado["agora"] = utc(2026, 11, 11, 3, 0)
    assert contador.tentar_registrar(900, 100, 1000) is None


def test_o_dia_e_o_de_sao_paulo_e_nao_o_de_utc():
    # 01:00 UTC do dia 11 ainda é noite do dia 10 em São Paulo: o contador de UTC já teria virado.
    estado, relogio = relogio_em(utc(2026, 11, 10, 23, 0))
    contador = ContadorDiario(relogio)
    assert contador.tentar_registrar(1, 1, 100) is None

    estado["agora"] = utc(2026, 11, 11, 1, 0)

    assert contador.tentar_registrar(1, 1, 100) == "documentos"


def test_a_virada_do_dia_libera_a_triagem_na_api(monkeypatch, novo_cliente):
    monkeypatch.setenv("MEDIFLOW_TETO_DIARIO_DOCUMENTOS", "1")
    estado, relogio = relogio_em(utc(2026, 11, 10, 15, 0))
    monkeypatch.setattr(CONTADOR_DIARIO, "_relogio", relogio)
    cliente = novo_cliente()

    assert enviar_triagem(cliente, 1).status_code == 200
    assert enviar_triagem(cliente, 2).status_code == 429

    estado["agora"] = utc(2026, 11, 11, 3, 0)

    assert enviar_triagem(cliente, 3).status_code == 200


def test_o_contador_nasce_vazio_como_depois_de_reiniciar_a_api():
    assert ContadorDiario().tentar_registrar(1, 1, 100) is None


def test_sem_a_base_de_fusos_o_dia_vira_em_utc_menos_3_e_a_api_avisa(monkeypatch, caplog):
    def sem_tzdata(nome):
        raise ZoneInfoNotFoundError(nome)

    monkeypatch.setattr(seguranca, "ZoneInfo", sem_tzdata)
    estado, relogio = relogio_em(utc(2026, 11, 11, 2, 59, 59))
    with caplog.at_level(logging.WARNING, logger="mediflow.api.seguranca"):
        contador = ContadorDiario(relogio)

    assert "UTC-3 fixo" in caplog.text
    assert contador.tentar_registrar(1, 1, 100) is None
    assert contador.tentar_registrar(1, 1, 100) == "documentos"
    estado["agora"] = utc(2026, 11, 11, 3, 0)
    assert contador.tentar_registrar(1, 1, 100) is None


def test_o_contador_nunca_passa_do_teto_com_varias_threads_ao_mesmo_tempo():
    contador = ContadorDiario()
    largada = threading.Barrier(64)

    def tentar(_):
        largada.wait()
        return contador.tentar_registrar(10, 10, 10_000)

    with ThreadPoolExecutor(max_workers=64) as pool:
        resultados = list(pool.map(tentar, range(64)))

    assert resultados.count(None) == 10  # exatamente o teto
    assert resultados.count("documentos") == 54


# --- Configuração dos tetos ---------------------------------------------------------------------

def test_os_padroes_dos_tetos_sao_100_documentos_e_300_mb():
    configuracao = ler_configuracao()

    assert configuracao.teto_diario_de_documentos == 100
    assert configuracao.teto_diario_de_bytes == 300 * 1024 * 1024


@pytest.mark.parametrize("documentos, mb, esperado_docs, esperado_bytes", [
    ("5", "", 5, 300 * 1024 * 1024),
    ("  7  ", "0.5", 7, 524288),
    ("", "1", 100, 1024 * 1024),
    ("1", "0.001", 1, 1048),
])
def test_tetos_validos_do_ambiente(monkeypatch, documentos, mb, esperado_docs, esperado_bytes):
    monkeypatch.setenv("MEDIFLOW_TETO_DIARIO_DOCUMENTOS", documentos)
    monkeypatch.setenv("MEDIFLOW_TETO_DIARIO_MB", mb)

    configuracao = ler_configuracao()

    assert (configuracao.teto_diario_de_documentos, configuracao.teto_diario_de_bytes) == (esperado_docs, esperado_bytes)


@pytest.mark.parametrize("variavel, valor", [
    ("MEDIFLOW_TETO_DIARIO_DOCUMENTOS", "abc"),
    ("MEDIFLOW_TETO_DIARIO_DOCUMENTOS", "0"),
    ("MEDIFLOW_TETO_DIARIO_DOCUMENTOS", "-3"),
    ("MEDIFLOW_TETO_DIARIO_DOCUMENTOS", "2.5"),
    ("MEDIFLOW_TETO_DIARIO_MB", "abc"),
    ("MEDIFLOW_TETO_DIARIO_MB", "0"),
    ("MEDIFLOW_TETO_DIARIO_MB", "-1"),
    ("MEDIFLOW_TETO_DIARIO_MB", "nan"),
    ("MEDIFLOW_TETO_DIARIO_MB", "inf"),
])
def test_teto_invalido_derruba_a_subida_e_na_requisicao_da_503(monkeypatch, novo_cliente, tmp_path, variavel, valor):
    monkeypatch.setenv(variavel, valor)

    with pytest.raises(ConfiguracaoInvalida, match=variavel):
        with TestClient(app):
            pass
    resposta = novo_cliente().post("/api/v1/triagem", json=entrada_de_triagem())  # sem lifespan
    assert resposta.status_code == 503
    assert nada_gravado(tmp_path)


# ============================================================================
# Documentação da configuração
# ============================================================================

def test_o_env_example_documenta_as_quatro_variaveis_sem_nenhum_valor():
    exemplo = dotenv_values(Path(__file__).resolve().parent.parent / ".env.example")

    for variavel in ("MEDIFLOW_API_KEY", "MEDIFLOW_API_AUTH", "MEDIFLOW_TETO_DIARIO_DOCUMENTOS", "MEDIFLOW_TETO_DIARIO_MB"):
        assert variavel in exemplo, f"{variavel} não está no .env.example"
        assert exemplo[variavel] == "", f"{variavel} não pode ter valor no .env.example"
