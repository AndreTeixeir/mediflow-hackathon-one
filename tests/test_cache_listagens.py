"""
MediFlow - Testes do cache das listagens da auditoria e do limite de 50 itens
Hackathon ONE G10 - Grupo 10: MediFlow

Mapeamento: tests/test_cache_listagens.py
Cada GET /api/v1/auditoria/pendentes e /api/v1/documentos lê o armazenamento (na OCI, cada leitura é uma
requisição da cota mensal). Estes testes usam um repositório que conta as chamadas a listar() e a
ler_json(), e um relógio injetável (sem sleep).
Execução (a partir da raiz): python -m pytest tests/test_cache_listagens.py -v
"""

import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from api.auditoria import LIMITE_DE_ITENS
from api.cache_listagens import LISTAGENS, VALIDADE_DO_CACHE_SEGUNDOS, CacheDeListagens
from api.main import app, obter_repositorio
from armazenamento.repositorio import FalhaArmazenamento, RepositorioLocal

RAIZ = Path(__file__).resolve().parent.parent
AUDITORIA = "mediflow-auditoria-humana"
ROTINA = "mediflow-rotina"
URGENTES = "mediflow-urgentes"
REJEITADOS = "mediflow-rejeitados"
QUATRO_BUCKETS = [ROTINA, URGENTES, AUDITORIA, REJEITADOS]
PENDENTES = "/api/v1/auditoria/pendentes"
DOCUMENTOS = "/api/v1/documentos"
LAUDO_SEM_CRM = (
    "LABORATORIO CENTRAL - LAUDO DE EXAME LABORATORIAL.\n"
    "Paciente: Mariana Alcantara de Souza, 34 anos.\n"
    "Solicitante: Dr. Roberto Viana.\n"
    "Exame: Hemograma completo.\n"
    "CONCLUSÃO: Parâmetros dentro dos limites da normalidade."
)


class RepositorioQueConta(RepositorioLocal):
    """RepositorioLocal que registra cada listagem e cada leitura, e que pode falhar ao listar."""

    def __init__(self, base):
        super().__init__(base)
        self.listagens = []  # um item por chamada a listar()
        self.leituras = []  # um item por chamada a ler_json()
        self.falhar_ao_listar = False

    def listar(self, bucket):
        self.listagens.append(bucket)
        if self.falhar_ao_listar:
            raise FalhaArmazenamento("detalhe interno que não pode vazar")
        return super().listar(bucket)

    def ler_json(self, bucket, nome_objeto):
        self.leituras.append((bucket, nome_objeto))
        return super().ler_json(bucket, nome_objeto)

    def zerar_contagem(self):
        self.listagens.clear()
        self.leituras.clear()


@pytest.fixture
def repositorio(tmp_path):
    return RepositorioQueConta(tmp_path)


@pytest.fixture
def cliente(repositorio, novo_cliente):
    app.dependency_overrides[obter_repositorio] = lambda: repositorio
    yield novo_cliente()
    app.dependency_overrides.clear()


@pytest.fixture
def relogio(monkeypatch):
    """Um relógio para o cache da API, que o teste avança sem sleep."""
    estado = {"t": 1000.0}
    monkeypatch.setattr(LISTAGENS, "_relogio", lambda: estado["t"])
    return estado


def cenario_de_rotina(documento_id):
    dado = json.loads((RAIZ / "dados_teste" / "1_rotina" / "exemplo.json").read_text(encoding="utf-8"))
    return dict(dado["requisicao_entrada"], documento_id=documento_id)


def laudo_retido(documento_id):
    return {"documento_id": documento_id, "documento_texto": LAUDO_SEM_CRM, "canal_origem": "Ambulatorio"}


def enviar(cliente, entrada):
    resposta = cliente.post("/api/v1/triagem", json=entrada)
    assert resposta.status_code == 200, resposta.text
    return resposta.json()


def decisao(destino="rotina"):
    return {"destino": destino, "revisor": "Lethicia", "justificativa": "Dados conferidos com o original."}


def ids(resposta):
    return [item["documento_id"] for item in resposta.json()]


# ============================================================================
# O armazenamento é lido uma vez só
# ============================================================================

def test_duas_chamadas_seguidas_a_fila_leem_o_armazenamento_uma_vez_so(cliente, repositorio):
    enviar(cliente, laudo_retido("DOC-A"))
    enviar(cliente, laudo_retido("DOC-B"))
    repositorio.zerar_contagem()

    primeira = cliente.get(PENDENTES)
    segunda = cliente.get(PENDENTES)

    assert primeira.status_code == segunda.status_code == 200
    assert primeira.json() == segunda.json()
    assert ids(primeira) == ["DOC-A", "DOC-B"]
    assert repositorio.listagens == [AUDITORIA]  # uma listagem...
    assert sorted(repositorio.leituras) == [(AUDITORIA, "DOC-A.json"), (AUDITORIA, "DOC-B.json")]  # ...e uma leitura por item


def test_duas_chamadas_seguidas_ao_historico_leem_o_armazenamento_uma_vez_so(cliente, repositorio):
    enviar(cliente, cenario_de_rotina("DOC-ROT"))
    enviar(cliente, laudo_retido("DOC-RET"))
    repositorio.zerar_contagem()

    primeira = cliente.get(DOCUMENTOS)
    segunda = cliente.get(DOCUMENTOS)

    assert primeira.json() == segunda.json()
    assert sorted(ids(primeira)) == ["DOC-RET", "DOC-ROT"]
    assert repositorio.listagens == QUATRO_BUCKETS  # os 4 buckets, uma vez cada
    assert len(repositorio.leituras) == 2


def test_cada_endpoint_tem_o_seu_cache(cliente, repositorio):
    enviar(cliente, laudo_retido("DOC-A"))
    repositorio.zerar_contagem()

    cliente.get(PENDENTES)
    cliente.get(DOCUMENTOS)  # outra chave: não aproveita a fila
    cliente.get(PENDENTES)
    cliente.get(DOCUMENTOS)

    assert repositorio.listagens == [AUDITORIA] + QUATRO_BUCKETS


# ============================================================================
# O resultado vale 60 s
# ============================================================================

@pytest.mark.parametrize("rota", [PENDENTES, DOCUMENTOS])
def test_o_resultado_vale_ate_60_segundos_e_depois_le_de_novo(cliente, repositorio, relogio, rota):
    assert VALIDADE_DO_CACHE_SEGUNDOS == 60
    enviar(cliente, laudo_retido("DOC-A"))
    repositorio.zerar_contagem()

    cliente.get(rota)
    primeira_leitura = list(repositorio.leituras)
    relogio["t"] += 59.9
    cliente.get(rota)

    assert repositorio.leituras == primeira_leitura  # aos 59,9 s ainda vale

    relogio["t"] += 0.1  # 60,0 s depois da primeira consulta
    cliente.get(rota)

    assert repositorio.leituras == primeira_leitura * 2  # leu de novo


def test_depois_de_expirar_a_nova_leitura_vale_por_mais_60_segundos(cliente, repositorio, relogio):
    enviar(cliente, laudo_retido("DOC-A"))
    cliente.get(PENDENTES)
    relogio["t"] += 60
    cliente.get(PENDENTES)
    repositorio.zerar_contagem()

    relogio["t"] += 59
    cliente.get(PENDENTES)

    assert repositorio.listagens == []


# ============================================================================
# Uma triagem 200 e uma decisão 200 limpam o cache e a mudança aparece na hora
# ============================================================================

def test_depois_de_uma_triagem_200_a_proxima_listagem_le_de_novo_e_ja_mostra_o_documento(cliente, repositorio):
    assert cliente.get(PENDENTES).json() == []
    assert cliente.get(DOCUMENTOS).json() == []
    repositorio.zerar_contagem()

    enviar(cliente, laudo_retido("DOC-NOVO"))
    fila = cliente.get(PENDENTES)
    historico = cliente.get(DOCUMENTOS)

    assert ids(fila) == ["DOC-NOVO"]
    assert ids(historico) == ["DOC-NOVO"]
    assert repositorio.listagens == [AUDITORIA] + QUATRO_BUCKETS  # leu de novo as duas


def test_depois_de_uma_decisao_200_a_proxima_listagem_le_de_novo_e_ja_mostra_a_mudanca(cliente, repositorio):
    enviar(cliente, laudo_retido("DOC-DECIDE"))
    enviar(cliente, laudo_retido("DOC-FICA"))
    assert ids(cliente.get(PENDENTES)) == ["DOC-DECIDE", "DOC-FICA"]
    antes = {h["documento_id"]: h["bucket"] for h in cliente.get(DOCUMENTOS).json()}
    assert antes == {"DOC-DECIDE": AUDITORIA, "DOC-FICA": AUDITORIA}
    repositorio.zerar_contagem()

    assert cliente.post("/api/v1/auditoria/DOC-DECIDE/decisao", json=decisao("urgente")).status_code == 200
    repositorio.zerar_contagem()  # a decisão também lê; o que interessa é o que as listagens fazem depois
    fila = cliente.get(PENDENTES)
    historico = cliente.get(DOCUMENTOS)

    assert ids(fila) == ["DOC-FICA"]  # o decidido já saiu da fila
    assert {h["documento_id"]: h["bucket"] for h in historico.json()} == {"DOC-DECIDE": URGENTES, "DOC-FICA": AUDITORIA}
    assert repositorio.listagens == [AUDITORIA] + QUATRO_BUCKETS


# ============================================================================
# O que não grava nada não limpa o cache
# ============================================================================

def esquentar(cliente, repositorio):
    """Um documento retido e as duas listagens já em cache, com a contagem zerada."""
    enviar(cliente, laudo_retido("DOC-BASE"))
    cliente.get(PENDENTES)
    cliente.get(DOCUMENTOS)
    repositorio.zerar_contagem()


def triagem_que_da_409(cliente):
    return cliente.post("/api/v1/triagem", json=laudo_retido("DOC-BASE"))  # o mesmo ID de novo


def triagem_que_da_422(cliente):
    return cliente.post("/api/v1/triagem", json={"documento_id": "DOC-INVALIDO"})  # sem texto nem Base64


def triagem_que_da_429(cliente, monkeypatch):
    monkeypatch.setenv("MEDIFLOW_TETO_DIARIO_DOCUMENTOS", "1")  # o DOC-BASE já gastou o teto
    return cliente.post("/api/v1/triagem", json=laudo_retido("DOC-ACIMA-DO-TETO"))


@pytest.mark.parametrize("codigo", [409, 422, 429])
def test_triagem_que_nao_deu_200_nao_limpa_o_cache(cliente, repositorio, monkeypatch, codigo):
    esquentar(cliente, repositorio)

    resposta = {409: lambda: triagem_que_da_409(cliente), 422: lambda: triagem_que_da_422(cliente),
                429: lambda: triagem_que_da_429(cliente, monkeypatch)}[codigo]()
    assert resposta.status_code == codigo
    fila = cliente.get(PENDENTES)
    historico = cliente.get(DOCUMENTOS)

    assert ids(fila) == ["DOC-BASE"] and ids(historico) == ["DOC-BASE"]
    assert repositorio.listagens == []  # nenhuma leitura nova: o cache continuou valendo


def test_triagem_que_da_503_nao_limpa_o_cache(cliente, repositorio, monkeypatch):
    esquentar(cliente, repositorio)

    def gravacao_que_falha(*args, **kwargs):
        raise FalhaArmazenamento("detalhe interno")

    monkeypatch.setattr(repositorio, "salvar_json", gravacao_que_falha)
    assert cliente.post("/api/v1/triagem", json=laudo_retido("DOC-503")).status_code == 503
    cliente.get(PENDENTES)

    assert repositorio.listagens == []


@pytest.mark.parametrize("caso, status", [("nao-existe", 404), ("conflito", 409), ("corpo-invalido", 422)])
def test_decisao_que_nao_deu_200_nao_limpa_o_cache(cliente, repositorio, caso, status):
    esquentar(cliente, repositorio)
    if caso == "conflito":
        repositorio.salvar_json(URGENTES, "DOC-BASE.json", {"previo": True})
    documento = "DOC-NAO-EXISTE" if caso == "nao-existe" else "DOC-BASE"
    corpo = {"destino": "aprovado"} if caso == "corpo-invalido" else decisao("urgente")

    resposta = cliente.post(f"/api/v1/auditoria/{documento}/decisao", json=corpo)
    repositorio.listagens.clear()
    cliente.get(PENDENTES)
    cliente.get(DOCUMENTOS)

    assert resposta.status_code == status
    assert repositorio.listagens == []  # o cache de antes continuou valendo


# ============================================================================
# O 503 do armazenamento não fica guardado
# ============================================================================

@pytest.mark.parametrize("rota", [PENDENTES, DOCUMENTOS])
def test_um_503_do_armazenamento_nao_fica_guardado(cliente, repositorio, rota):
    enviar(cliente, laudo_retido("DOC-A"))
    repositorio.zerar_contagem()

    repositorio.falhar_ao_listar = True
    falha = cliente.get(rota)
    repositorio.falhar_ao_listar = False
    recuperada = cliente.get(rota)
    repositorio.zerar_contagem()
    de_novo = cliente.get(rota)

    assert falha.status_code == 503
    assert falha.json() == {"detail": "Falha ao acessar o armazenamento."}
    assert recuperada.status_code == 200 and ids(recuperada) == ["DOC-A"]  # a falha não foi guardada
    assert de_novo.json() == recuperada.json()
    assert repositorio.listagens == []  # e o resultado bom, esse sim, ficou guardado


# ============================================================================
# O limite de 50 itens e as leituras
# ============================================================================

def clonar(repositorio, modelo, bucket, quantidade, prefixo="DOC-LOTE"):
    for numero in range(quantidade):
        documento_id = f"{prefixo}-{numero:03d}"
        repositorio.salvar_json(bucket, f"{documento_id}.json", dict(modelo, documento_id=documento_id))


def test_o_limite_de_itens_e_50():
    assert LIMITE_DE_ITENS == 50
    codigo_do_painel = (RAIZ / "interface" / "app.py").read_text(encoding="utf-8")
    assert re.search(r"^LIMITE_DA_API = 50$", codigo_do_painel, flags=re.MULTILINE)


def test_com_60_objetos_na_fila_devolve_50_e_le_no_maximo_50(cliente, repositorio, tmp_path):
    modelo = enviar(cliente, laudo_retido("DOC-MODELO"))
    (tmp_path / AUDITORIA / "DOC-MODELO.json").unlink()  # só o lote entra na conta
    clonar(repositorio, modelo, AUDITORIA, 60)
    repositorio.zerar_contagem()
    LISTAGENS.limpar()

    resposta = cliente.get(PENDENTES)

    assert len(resposta.json()) == 50
    assert repositorio.listagens == [AUDITORIA]
    assert len(repositorio.leituras) <= 50  # nunca lê os 60


def test_com_60_objetos_no_historico_devolve_50_e_le_no_maximo_50(cliente, repositorio, tmp_path):
    modelo = enviar(cliente, cenario_de_rotina("DOC-MODELO"))
    (tmp_path / ROTINA / "DOC-MODELO.json").unlink()
    clonar(repositorio, modelo, ROTINA, 60)
    repositorio.zerar_contagem()
    LISTAGENS.limpar()

    resposta = cliente.get(DOCUMENTOS)

    assert len(resposta.json()) == 50
    assert repositorio.listagens == QUATRO_BUCKETS
    assert len(repositorio.leituras) <= 50


def test_o_historico_devolve_os_50_mais_recentes_entre_os_buckets(cliente, repositorio, tmp_path):
    modelo = enviar(cliente, cenario_de_rotina("DOC-MODELO"))
    (tmp_path / ROTINA / "DOC-MODELO.json").unlink()
    clonar(repositorio, modelo, ROTINA, 30, prefixo="DOC-ROT")
    clonar(repositorio, modelo, URGENTES, 30, prefixo="DOC-URG")
    for numero in range(30):
        os.utime(tmp_path / ROTINA / f"DOC-ROT-{numero:03d}.json", (1000 + numero, 1000 + numero))
        os.utime(tmp_path / URGENTES / f"DOC-URG-{numero:03d}.json", (5000 + numero, 5000 + numero))
    repositorio.zerar_contagem()
    LISTAGENS.limpar()

    historico = ids(cliente.get(DOCUMENTOS))

    # Os 30 de urgentes (mais novos) e os 20 mais novos de rotina (10 a 29).
    assert len(historico) == 50
    assert {i for i in historico if i.startswith("DOC-URG")} == {f"DOC-URG-{n:03d}" for n in range(30)}
    assert {i for i in historico if i.startswith("DOC-ROT")} == {f"DOC-ROT-{n:03d}" for n in range(10, 30)}
    assert len(repositorio.leituras) == 50


# ============================================================================
# A classe do cache
# ============================================================================

def cache_com_relogio(validade=60):
    estado = {"t": 0.0}
    return CacheDeListagens(validade=validade, relogio=lambda: estado["t"]), estado


def calculadora(valores):
    """Devolve (calcular, chamadas): cada chamada a calcular() devolve o próximo valor da lista."""
    chamadas = []

    def calcular():
        chamadas.append(1)
        return valores[len(chamadas) - 1]

    return calcular, chamadas


def test_o_cache_calcula_uma_vez_e_depois_devolve_o_guardado():
    cache, _ = cache_com_relogio()
    calcular, chamadas = calculadora(["a", "b"])

    assert cache.obter("k", calcular) == "a"
    assert cache.obter("k", calcular) == "a"
    assert len(chamadas) == 1


def test_o_cache_expira_na_validade_exata():
    cache, relogio = cache_com_relogio(validade=60)
    calcular, chamadas = calculadora(["a", "b", "c"])

    cache.obter("k", calcular)
    relogio["t"] = 59.999
    assert cache.obter("k", calcular) == "a"
    relogio["t"] = 60.0
    assert cache.obter("k", calcular) == "b"
    relogio["t"] = 119.9
    assert cache.obter("k", calcular) == "b"  # a nova validade conta a partir do novo cálculo
    assert len(chamadas) == 2


def test_chaves_diferentes_sao_independentes():
    cache, _ = cache_com_relogio()

    assert cache.obter("pendentes", lambda: "fila") == "fila"
    assert cache.obter("documentos", lambda: "historico") == "historico"
    assert cache.obter("pendentes", lambda: "outra") == "fila"


def test_limpar_forca_um_novo_calculo():
    cache, _ = cache_com_relogio()
    calcular, chamadas = calculadora(["a", "b"])
    cache.obter("k", calcular)

    cache.limpar()

    assert cache.obter("k", calcular) == "b"
    assert len(chamadas) == 2


def test_excecao_no_calculo_nao_e_guardada():
    cache, _ = cache_com_relogio()

    def falha():
        raise RuntimeError("armazenamento fora do ar")

    with pytest.raises(RuntimeError):
        cache.obter("k", falha)

    assert cache.obter("k", lambda: "recuperado") == "recuperado"


def test_uma_limpeza_durante_o_calculo_impede_que_o_resultado_velho_fique_guardado():
    # Uma listagem começa, uma gravação limpa o cache, e a listagem termina com o que leu ANTES da gravação:
    # esse resultado serve a quem o pediu, mas não pode ficar valendo por 60 s.
    cache, _ = cache_com_relogio()

    def calcular_com_gravacao_no_meio():
        cache.limpar()
        return "velho"

    assert cache.obter("k", calcular_com_gravacao_no_meio) == "velho"
    assert cache.obter("k", lambda: "novo") == "novo"


def test_sem_limpeza_no_meio_o_resultado_fica_guardado():
    cache, _ = cache_com_relogio()

    cache.obter("k", lambda: "a")

    assert cache.obter("k", lambda: "b") == "a"


def test_o_limpar_nao_espera_um_calculo_lento_terminar():
    # O cálculo (as leituras no armazenamento) roda fora do lock: uma triagem não pode ficar presa atrás
    # de uma listagem lenta.
    cache, _ = cache_com_relogio()
    calculo_comecou, liberar = threading.Event(), threading.Event()
    resultado = {}

    def calcular_devagar():
        calculo_comecou.set()
        liberar.wait(timeout=10)
        return "lento"

    leitora = threading.Thread(target=lambda: resultado.update(valor=cache.obter("k", calcular_devagar)))
    leitora.start()
    assert calculo_comecou.wait(timeout=5)

    limpeza = threading.Thread(target=cache.limpar)
    limpeza.start()
    limpeza.join(timeout=2)
    terminou_sem_esperar = not limpeza.is_alive()
    liberar.set()
    leitora.join(timeout=5)

    assert terminou_sem_esperar
    assert resultado["valor"] == "lento"
    assert cache.obter("k", lambda: "depois") == "depois"  # o resultado lento era de antes da limpeza


def test_varias_threads_ao_mesmo_tempo_recebem_sempre_um_valor_valido():
    cache, _ = cache_com_relogio()
    largada = threading.Barrier(32)
    calculos = []

    def pedir(_):
        largada.wait()
        return cache.obter("k", lambda: calculos.append(1) or "valor")

    with ThreadPoolExecutor(max_workers=32) as pool:
        resultados = list(pool.map(pedir, range(32)))

    assert resultados == ["valor"] * 32
    assert 1 <= len(calculos) <= 32  # numa rajada com o cache vazio mais de um pode calcular
    antes = len(calculos)
    assert cache.obter("k", lambda: calculos.append(1) or "outro") == "valor"
    assert len(calculos) == antes  # depois da rajada, ficou guardado


def test_limpar_e_obter_ao_mesmo_tempo_nao_quebram():
    cache, _ = cache_com_relogio()
    parar = threading.Event()
    erros = []

    def limpar_sem_parar():
        while not parar.is_set():
            cache.limpar()

    limpadora = threading.Thread(target=limpar_sem_parar)
    limpadora.start()
    try:
        for _ in range(2000):
            try:
                assert cache.obter("k", lambda: "v") == "v"
            except Exception as erro:  # noqa: BLE001  (o teste só quer saber se algo quebrou)
                erros.append(erro)
    finally:
        parar.set()
        limpadora.join(timeout=5)

    assert erros == []
