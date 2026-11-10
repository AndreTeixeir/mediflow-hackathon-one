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
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import api.seguranca as seguranca
from api.main import app, obter_repositorio
from api.seguranca import ConfiguracaoInvalida, SegurancaMiddleware, ler_configuracao
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
