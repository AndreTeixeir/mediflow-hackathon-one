"""
MediFlow - Testes da API de triagem
Hackathon ONE G10 - Grupo 10: MediFlow

Mapeamento: tests/test_api.py
Rodam sem credenciais OCI: o repositório é substituído por um RepositorioLocal em pasta
temporária (app.dependency_overrides). O .env de quem roda não influencia o resultado.
Execução (a partir da raiz): python -m pytest tests/test_api.py -v
"""

import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.triagem as modulo_triagem
from api.main import app, obter_repositorio
from armazenamento.repositorio import FalhaArmazenamento, RepositorioLocal
from regras.regras_clinicas import TERMOS_PRIORITARIOS, TERMOS_URGENCIA_CRITICA

PASTA_DADOS = Path(__file__).resolve().parent.parent / "dados_teste"

VARIAVEIS = (
    "LLM_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "MEDIFLOW_ARMAZENAMENTO",
    "MEDIFLOW_ARMAZENAMENTO_LOCAL_DIR",
    "OCI_AUTH",
    "OCI_CONFIG_FILE",
    "OCI_CONFIG_PROFILE",
    "OCI_BUCKET_PREFIXO",
    "OCI_NAMESPACE",
)

# (pasta do cenário, destino, status, bucket de destino)
CENARIOS = [
    ("1_rotina", "Prontuario_Eletronico_do_Paciente", "processado", "mediflow-rotina"),
    ("2_urgencia", "Fila_Emergencia_Medica", "processado", "mediflow-urgentes"),
    ("3_auditoria_humana", "Fila_Revisao_Humana", "pendente_auditoria", "mediflow-auditoria-humana"),
]

# Sem nenhum termo de urgência das regras: a busca delas é por substring ("tep", "iam", "pcr"
# casam dentro de outras palavras). O primeiro teste que usa este texto confere isso.
TEXTO_DESCONHECIDO = "Aviso interno: a reuniao de equipe foi remarcada para a proxima semana na sala de treinamento."


@pytest.fixture(autouse=True)
def ambiente_limpo(monkeypatch):
    # contratos/modelos.py chama load_dotenv() ao ser importado: o .env de quem roda os
    # testes (por exemplo, MEDIFLOW_ARMAZENAMENTO=oci) não pode influenciar o resultado.
    for variavel in VARIAVEIS:
        monkeypatch.delenv(variavel, raising=False)


@pytest.fixture
def repositorio(tmp_path):
    return RepositorioLocal(tmp_path)


@pytest.fixture
def cliente(repositorio):
    app.dependency_overrides[obter_repositorio] = lambda: repositorio
    yield TestClient(app)
    app.dependency_overrides.clear()


def carregar_cenario(pasta):
    with open(PASTA_DADOS / pasta / "exemplo.json", encoding="utf-8") as f:
        return json.load(f)


def lido(tmp_path, bucket, documento_id):
    return json.loads((tmp_path / bucket / f"{documento_id}.json").read_text(encoding="utf-8"))


# ============================================================================
# Saúde
# ============================================================================

def test_saude_responde_ok_com_o_armazenamento_em_uso(cliente):
    resposta = cliente.get("/api/v1/saude")

    assert resposta.status_code == 200
    assert resposta.json() == {"status": "ok", "armazenamento": "local"}


# ============================================================================
# Os 3 cenários oficiais
# ============================================================================

@pytest.mark.parametrize("pasta, destino, status, bucket", CENARIOS)
def test_cenario_oficial(cliente, tmp_path, pasta, destino, status, bucket):
    cenario = carregar_cenario(pasta)
    entrada = cenario["requisicao_entrada"]
    documento_id = entrada["documento_id"]

    resposta = cliente.post("/api/v1/triagem", json=entrada)

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["documento_id"] == documento_id
    assert corpo["decisao_roteamento"]["destino_principal"] == destino
    assert corpo["status"] == status
    assert corpo["classificacao"]["tipo_documento"] == cenario["resposta_esperada"]["classificacao"]["tipo_documento"]
    assert corpo["armazenamento_oci"] == {
        "bucket": bucket,
        "caminho_objeto": f"{documento_id}.json",
        "status_backup": "sucesso",
    }

    # Exatamente dois objetos: a entrada em recebidos e a resposta no bucket de destino.
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(["mediflow-recebidos", bucket])
    assert lido(tmp_path, "mediflow-recebidos", documento_id)["documento_id"] == documento_id
    assert lido(tmp_path, "mediflow-recebidos", documento_id)["documento_texto"] == entrada["documento_texto"]
    assert lido(tmp_path, bucket, documento_id) == corpo


def test_cenario_urgencia_gera_notificacao(cliente):
    entrada = carregar_cenario("2_urgencia")["requisicao_entrada"]

    corpo = cliente.post("/api/v1/triagem", json=entrada).json()

    notificacao = corpo["decisao_roteamento"]["notificacao_gerada"]
    assert notificacao is not None
    assert notificacao["canal"] == "Alerta_Plantao_Medico"
    assert "TEP" in notificacao["mensagem"]


def test_cenario_auditoria_exige_revisao_humana(cliente):
    entrada = carregar_cenario("3_auditoria_humana")["requisicao_entrada"]

    corpo = cliente.post("/api/v1/triagem", json=entrada).json()

    assert corpo["decisao_roteamento"]["requer_auditoria_humana"] is True
    assert corpo["decisao_roteamento"]["notificacao_gerada"] is None


def test_texto_com_espacos_e_quebras_diferentes_ainda_e_reconhecido(cliente):
    entrada = dict(carregar_cenario("2_urgencia")["requisicao_entrada"])
    entrada["documento_id"] = "DOC-ESPACOS"
    entrada["documento_texto"] = "  " + entrada["documento_texto"].replace(" ", "  \n") + "  "

    corpo = cliente.post("/api/v1/triagem", json=entrada).json()

    assert corpo["classificacao"]["tipo_documento"] == "Laudo de Exame de Imagem"
    assert corpo["decisao_roteamento"]["destino_principal"] == "Fila_Emergencia_Medica"


# ============================================================================
# Documento desconhecido
# ============================================================================

def test_documento_desconhecido_vai_para_revisao_humana(cliente, tmp_path):
    corpo_texto = TEXTO_DESCONHECIDO.lower()
    assert not any(termo in corpo_texto for termo in TERMOS_URGENCIA_CRITICA + TERMOS_PRIORITARIOS)

    resposta = cliente.post(
        "/api/v1/triagem", json={"documento_id": "DOC-DESCONHECIDO", "documento_texto": TEXTO_DESCONHECIDO}
    )

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["status"] == "pendente_auditoria"
    assert corpo["decisao_roteamento"]["destino_principal"] == "Fila_Revisao_Humana"
    assert corpo["classificacao"]["score_confianca_classificacao"] == 0.0
    assert corpo["armazenamento_oci"]["bucket"] == "mediflow-auditoria-humana"
    assert (tmp_path / "mediflow-auditoria-humana" / "DOC-DESCONHECIDO.json").exists()


def test_documento_so_com_base64_vai_para_revisao_humana(cliente):
    resposta = cliente.post(
        "/api/v1/triagem", json={"documento_id": "DOC-BASE64", "tipo_arquivo": "PNG", "documento_base64": "iVBORw0KGgo="}
    )

    assert resposta.status_code == 200, resposta.text
    assert resposta.json()["status"] == "pendente_auditoria"
    assert resposta.json()["armazenamento_oci"]["bucket"] == "mediflow-auditoria-humana"


# ============================================================================
# Duplicidade
# ============================================================================

def test_mesmo_documento_id_duas_vezes_da_409_e_preserva_o_primeiro(cliente, tmp_path):
    entrada = carregar_cenario("1_rotina")["requisicao_entrada"]
    documento_id = entrada["documento_id"]
    primeira = cliente.post("/api/v1/triagem", json=entrada)
    assert primeira.status_code == 200
    arquivos_antes = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}

    # Segundo envio com o mesmo ID, mas de outro cenário (urgência): nada pode mudar.
    outra = dict(carregar_cenario("2_urgencia")["requisicao_entrada"], documento_id=documento_id)
    segunda = cliente.post("/api/v1/triagem", json=outra)

    assert segunda.status_code == 409
    assert segunda.json() == {"detail": "Documento com este documento_id já foi recebido."}
    assert {p: p.read_bytes() for p in tmp_path.rglob("*.json")} == arquivos_antes
    assert not (tmp_path / "mediflow-urgentes").exists()


# ============================================================================
# Validações da API
# ============================================================================

@pytest.mark.parametrize(
    "documento_id",
    ["../etc", "..", "a/b", "a\\b", "", "A" * 65, "com espaco", "acentuação", "ABC\n"],
)
def test_documento_id_invalido_da_422_sem_gravar_nada(cliente, tmp_path, documento_id):
    resposta = cliente.post("/api/v1/triagem", json={"documento_id": documento_id, "documento_texto": "texto"})

    assert resposta.status_code == 422
    assert "documento_id" in resposta.text
    assert list(tmp_path.iterdir()) == []


def test_documento_id_no_limite_de_64_caracteres_e_aceito(cliente):
    resposta = cliente.post(
        "/api/v1/triagem", json={"documento_id": "A" * 63 + "-", "documento_texto": TEXTO_DESCONHECIDO}
    )

    assert resposta.status_code == 200


@pytest.mark.parametrize(
    "corpo",
    [
        {"documento_id": "DOC-SEM-CONTEUDO"},
        {"documento_id": "DOC-SEM-CONTEUDO", "documento_texto": ""},
        {"documento_id": "DOC-SEM-CONTEUDO", "documento_texto": "   \n  "},
        {"documento_id": "DOC-SEM-CONTEUDO", "documento_base64": ""},
        {"documento_id": "DOC-SEM-CONTEUDO", "documento_texto": "", "documento_base64": "  "},
    ],
)
def test_sem_texto_e_sem_base64_da_422_sem_gravar_nada(cliente, tmp_path, corpo):
    resposta = cliente.post("/api/v1/triagem", json=corpo)

    assert resposta.status_code == 422
    assert list(tmp_path.iterdir()) == []


def test_corpo_sem_documento_id_da_422(cliente):
    assert cliente.post("/api/v1/triagem", json={"documento_texto": "texto"}).status_code == 422


# ============================================================================
# Falhas de armazenamento
# ============================================================================

class RepositorioFalho:
    """Falha na N-ésima gravação (1 = a entrada em recebidos, 2 = a resposta no destino)."""

    nome = "falho"

    def __init__(self, falha_na_gravacao):
        self.falha_na_gravacao = falha_na_gravacao
        self.gravacoes = 0

    def salvar_json(self, bucket, nome_objeto, conteudo):
        self.gravacoes += 1
        if self.gravacoes == self.falha_na_gravacao:
            raise FalhaArmazenamento("detalhe interno que não pode vazar")


@pytest.mark.parametrize("falha_na_gravacao", [1, 2])
def test_falha_do_repositorio_da_503_e_nunca_afirma_sucesso(falha_na_gravacao):
    app.dependency_overrides[obter_repositorio] = lambda: RepositorioFalho(falha_na_gravacao)
    try:
        resposta = TestClient(app).post(
            "/api/v1/triagem", json=carregar_cenario("1_rotina")["requisicao_entrada"]
        )
    finally:
        app.dependency_overrides.clear()

    assert resposta.status_code == 503
    assert resposta.json() == {"detail": "Falha ao gravar o documento no armazenamento."}
    assert "sucesso" not in resposta.text
    assert "detalhe interno" not in resposta.text


# ============================================================================
# Compensação: falha depois de gravar a entrada em `recebidos`
# ============================================================================

class RepositorioInstrumentado(RepositorioLocal):
    """RepositorioLocal com falhas ligáveis por atributo e registro das chamadas a apagar()."""

    def __init__(self, base):
        super().__init__(base)
        self.bucket_com_falha_ao_salvar = None
        self.falha_ao_apagar = False
        self.chamadas_apagar = []

    def salvar_json(self, bucket, nome_objeto, conteudo):
        if bucket == self.bucket_com_falha_ao_salvar:
            raise FalhaArmazenamento("detalhe interno da gravação")
        super().salvar_json(bucket, nome_objeto, conteudo)

    def apagar(self, bucket, nome_objeto):
        self.chamadas_apagar.append((bucket, nome_objeto))
        if self.falha_ao_apagar:
            raise FalhaArmazenamento("detalhe interno da compensação")
        super().apagar(bucket, nome_objeto)


@pytest.fixture
def repo_instrumentado(tmp_path):
    return RepositorioInstrumentado(tmp_path)


@pytest.fixture
def cliente_instrumentado(repo_instrumentado):
    app.dependency_overrides[obter_repositorio] = lambda: repo_instrumentado
    # raise_server_exceptions=False: devolve o 500 que um cliente real veria,
    # em vez de relançar a exceção dentro do teste.
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def entrada_rotina():
    return carregar_cenario("1_rotina")["requisicao_entrada"]


def registros_da_triagem(caplog, nivel):
    return [r for r in caplog.records if r.name == "mediflow.api.triagem" and r.levelno == nivel]


def test_falha_so_no_destino_da_503_e_desfaz_a_entrada(cliente_instrumentado, repo_instrumentado, tmp_path):
    entrada = entrada_rotina()
    documento_id = entrada["documento_id"]
    repo_instrumentado.bucket_com_falha_ao_salvar = "mediflow-rotina"

    resposta = cliente_instrumentado.post("/api/v1/triagem", json=entrada)

    assert resposta.status_code == 503
    assert resposta.json() == {"detail": "Falha ao gravar o documento no armazenamento."}
    assert "sucesso" not in resposta.text
    assert not (tmp_path / "mediflow-recebidos" / f"{documento_id}.json").exists()
    assert repo_instrumentado.chamadas_apagar == [("mediflow-recebidos", f"{documento_id}.json")]
    assert list(tmp_path.rglob("*.json")) == []


def test_reenvio_do_mesmo_documento_id_depois_da_falha_no_destino_da_200(
    cliente_instrumentado, repo_instrumentado, tmp_path
):
    entrada = entrada_rotina()
    documento_id = entrada["documento_id"]
    repo_instrumentado.bucket_com_falha_ao_salvar = "mediflow-rotina"
    assert cliente_instrumentado.post("/api/v1/triagem", json=entrada).status_code == 503

    repo_instrumentado.bucket_com_falha_ao_salvar = None  # o armazenamento se recuperou
    reenvio = cliente_instrumentado.post("/api/v1/triagem", json=entrada)

    assert reenvio.status_code == 200, reenvio.text
    assert reenvio.json()["armazenamento_oci"]["bucket"] == "mediflow-rotina"
    assert lido(tmp_path, "mediflow-recebidos", documento_id)["documento_id"] == documento_id
    assert lido(tmp_path, "mediflow-rotina", documento_id) == reenvio.json()


def test_falha_no_destino_e_na_compensacao_continua_503_e_registra_log_seguro(
    cliente_instrumentado, repo_instrumentado, tmp_path, caplog
):
    entrada = entrada_rotina()
    documento_id = entrada["documento_id"]
    repo_instrumentado.bucket_com_falha_ao_salvar = "mediflow-rotina"
    repo_instrumentado.falha_ao_apagar = True

    with caplog.at_level(logging.ERROR, logger="mediflow.api.triagem"):
        resposta = cliente_instrumentado.post("/api/v1/triagem", json=entrada)

    assert resposta.status_code == 503
    assert "sucesso" not in resposta.text
    assert "detalhe interno" not in resposta.text
    erros = registros_da_triagem(caplog, logging.ERROR)
    assert len(erros) == 1
    mensagem = erros[0].getMessage()
    assert documento_id in mensagem
    assert "mediflow-recebidos" in mensagem
    assert "detalhe interno" not in mensagem  # nada da mensagem original
    assert erros[0].exc_info is None  # sem traceback no log
    # Caso raro documentado: a entrada fica presa e precisa de limpeza manual.
    assert (tmp_path / "mediflow-recebidos" / f"{documento_id}.json").exists()


@pytest.mark.parametrize("alvo", ["analisar_documento", "executar_roteamento_clinico", "TriagemResposta"])
def test_excecao_inesperada_da_500_sem_detalhes_e_desfaz_a_entrada(
    cliente_instrumentado, repo_instrumentado, tmp_path, monkeypatch, alvo
):
    entrada = entrada_rotina()
    documento_id = entrada["documento_id"]

    def quebrar(*args, **kwargs):
        raise RuntimeError("segredo interno do bug")

    monkeypatch.setattr(modulo_triagem, alvo, quebrar)

    resposta = cliente_instrumentado.post("/api/v1/triagem", json=entrada)

    assert resposta.status_code == 500
    assert resposta.text == "Internal Server Error"
    assert "segredo interno" not in resposta.text
    assert "Traceback" not in resposta.text
    assert list(tmp_path.rglob("*.json")) == []
    assert repo_instrumentado.chamadas_apagar == [("mediflow-recebidos", f"{documento_id}.json")]


def test_excecao_inesperada_com_compensacao_falhando_relanca_a_original(
    cliente_instrumentado, repo_instrumentado, tmp_path, monkeypatch, caplog
):
    entrada = entrada_rotina()
    documento_id = entrada["documento_id"]
    repo_instrumentado.falha_ao_apagar = True

    def quebrar(*args, **kwargs):
        raise RuntimeError("segredo interno do bug")

    monkeypatch.setattr(modulo_triagem, "analisar_documento", quebrar)

    with caplog.at_level(logging.ERROR, logger="mediflow.api.triagem"):
        resposta = cliente_instrumentado.post("/api/v1/triagem", json=entrada)

    # 500 (a exceção original), e não 503 (a da compensação).
    assert resposta.status_code == 500
    assert resposta.text == "Internal Server Error"
    erros = registros_da_triagem(caplog, logging.ERROR)
    assert len(erros) == 1
    assert documento_id in erros[0].getMessage()
    assert "detalhe interno" not in erros[0].getMessage()
    assert "segredo interno" not in erros[0].getMessage()


def test_destino_ja_existente_da_409_nao_apaga_a_entrada_e_registra_aviso(
    cliente_instrumentado, repo_instrumentado, tmp_path, caplog
):
    entrada = entrada_rotina()
    documento_id = entrada["documento_id"]
    repo_instrumentado.salvar_json("mediflow-rotina", f"{documento_id}.json", {"previo": True})

    with caplog.at_level(logging.WARNING, logger="mediflow.api.triagem"):
        resposta = cliente_instrumentado.post("/api/v1/triagem", json=entrada)

    assert resposta.status_code == 409
    assert resposta.json() == {"detail": "Documento com este documento_id já foi recebido."}
    assert repo_instrumentado.chamadas_apagar == []
    assert (tmp_path / "mediflow-recebidos" / f"{documento_id}.json").exists()
    assert lido(tmp_path, "mediflow-rotina", documento_id) == {"previo": True}
    avisos = registros_da_triagem(caplog, logging.WARNING)
    assert len(avisos) == 1
    assert documento_id in avisos[0].getMessage()


def test_falha_ao_gravar_a_propria_entrada_da_503_sem_tentar_apagar(
    cliente_instrumentado, repo_instrumentado, tmp_path
):
    repo_instrumentado.bucket_com_falha_ao_salvar = "mediflow-recebidos"

    resposta = cliente_instrumentado.post("/api/v1/triagem", json=entrada_rotina())

    assert resposta.status_code == 503
    assert repo_instrumentado.chamadas_apagar == []  # nada foi gravado, nada a desfazer
    assert list(tmp_path.rglob("*.json")) == []


def test_entrada_duplicada_da_409_sem_compensacao(cliente_instrumentado, repo_instrumentado, tmp_path):
    entrada = entrada_rotina()
    assert cliente_instrumentado.post("/api/v1/triagem", json=entrada).status_code == 200
    arquivos_antes = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}

    segunda = cliente_instrumentado.post("/api/v1/triagem", json=entrada)

    assert segunda.status_code == 409
    assert repo_instrumentado.chamadas_apagar == []  # a entrada é de outra requisição: não se apaga
    assert {p: p.read_bytes() for p in tmp_path.rglob("*.json")} == arquivos_antes
