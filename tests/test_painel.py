"""
MediFlow - Testes do painel Streamlit (aba de envio)
Hackathon ONE G10 - Grupo 10: MediFlow

Mapeamento: tests/test_painel.py
O painel roda no AppTest do Streamlit, no mesmo processo do teste. As chamadas HTTP
(requests.post) são substituídas por uma função falsa: nenhuma rede, nenhuma API de verdade.
Execução (a partir da raiz): python -m pytest tests/test_painel.py -v
"""

import ast
import base64
import json
from pathlib import Path

import pytest
import requests
from streamlit.testing.v1 import AppTest

RAIZ = Path(__file__).resolve().parent.parent
CAMINHO_DO_PAINEL = str(RAIZ / "interface" / "app.py")
URL_DA_API = "http://api-de-teste:8000"
MODO_ARQUIVO = "Enviar arquivo PDF ou imagem"
UM_MEGABYTE = 1024 * 1024

TRIAGEM_OK = {
    "status": "processado",
    "documento_id": "DOC-TESTE-1",
    "classificacao": {
        "tipo_documento": "Laudo de Exame Laboratorial",
        "especialidade": "Geral",
        "nivel_prioridade": "Rotina",
        "score_confianca_classificacao": 0.9,
    },
    "dados_extraidos": {"paciente": {"nome": "Paciente Exemplo", "idade": 40}},
    "decisao_roteamento": {
        "destino_principal": "Prontuario_Eletronico_do_Paciente",
        "requer_auditoria_humana": False,
        "justificativa_roteamento": "Documento de rotina.",
        "notificacao_gerada": None,
    },
    "armazenamento_oci": {"bucket": "mediflow-rotina", "caminho_objeto": "DOC-TESTE-1.json", "status_backup": "sucesso"},
}


class RespostaFalsa:
    def __init__(self, status_code=200, corpo=None, texto=""):
        self.status_code = status_code
        self._corpo = corpo
        self.text = texto

    def json(self):
        if self._corpo is None:
            raise ValueError("sem JSON")
        return self._corpo


class ApiFalsa:
    """Substitui requests.post: guarda as chamadas e devolve (ou levanta) o que o teste pediu."""

    def __init__(self):
        self.chamadas = []
        self.resposta = RespostaFalsa(200, TRIAGEM_OK)
        self.erro = None

    def __call__(self, url, **kwargs):
        self.chamadas.append((url, kwargs))
        if self.erro is not None:
            raise self.erro
        return self.resposta


@pytest.fixture(autouse=True)
def ambiente_do_painel(monkeypatch):
    # O painel chama load_dotenv(): o .env de quem roda os testes não pode mudar o endereço da API.
    monkeypatch.setenv("MEDIFLOW_API_URL", URL_DA_API)


@pytest.fixture
def api(monkeypatch):
    falsa = ApiFalsa()
    monkeypatch.setattr(requests, "post", falsa)
    return falsa


@pytest.fixture
def painel(api):
    app = AppTest.from_file(CAMINHO_DO_PAINEL, default_timeout=30)
    app.run()
    assert not app.exception, [e.value for e in app.exception]
    return app


def mensagens(elementos):
    return [e.value for e in elementos]


def enviar_texto(painel, texto="Laudo fictício de teste. Paciente: Exemplo."):
    painel.text_area(key="texto_envio").set_value(texto)
    painel.button(key="btn_processar").click().run()
    return painel


def enviar_arquivo(painel, nome, conteudo):
    painel.radio(key="modo_envio").set_value(MODO_ARQUIVO).run()
    painel.file_uploader(key="arquivo_envio").set_value((nome, conteudo, "application/octet-stream"))
    painel.button(key="btn_processar").click().run()
    return painel


# ============================================================================
# ID do documento
# ============================================================================

def test_id_inicial_e_gerado_uma_vez_e_nao_muda_entre_execucoes(painel):
    primeiro = painel.text_input(key="documento_id").value

    painel.run()
    painel.run()

    assert primeiro.startswith("DOC-DEMO-")
    assert painel.text_input(key="documento_id").value == primeiro


def test_id_digitado_pela_pessoa_nao_e_descartado(painel):
    painel.text_input(key="documento_id").set_value("MEU-ID-DIGITADO").run()
    painel.run()

    assert painel.text_input(key="documento_id").value == "MEU-ID-DIGITADO"


def test_botao_gerar_novo_id_troca_o_id(painel):
    antes = painel.text_input(key="documento_id").value

    painel.button(key="btn_novo_id").click().run()

    depois = painel.text_input(key="documento_id").value
    assert depois != antes
    assert depois.startswith("DOC-DEMO-")


# ============================================================================
# Envio de texto e de arquivo
# ============================================================================

def test_envio_de_texto_vai_em_json_pela_api_com_tipo_txt(painel, api):
    painel.text_input(key="documento_id").set_value("DOC-TEXTO-1")
    enviar_texto(painel, "Texto fictício do documento.")

    assert len(api.chamadas) == 1
    url, kwargs = api.chamadas[0]
    assert url == f"{URL_DA_API}/api/v1/triagem"
    assert kwargs["timeout"] == 90
    assert kwargs["json"] == {
        "documento_id": "DOC-TEXTO-1",
        "canal_origem": "Pronto_Socorro",
        "tipo_arquivo": "TXT",
        "documento_texto": "Texto fictício do documento.",
    }


@pytest.mark.parametrize(
    "nome, tipo",
    [("laudo.pdf", "PDF"), ("foto.PNG", "PNG"), ("foto.jpg", "JPG"), ("foto.jpeg", "JPEG"), ("foto.webp", "WEBP")],
)
def test_envio_de_arquivo_vai_em_base64_com_o_tipo_pela_extensao(painel, api, nome, tipo):
    conteudo = b"bytes ficticios do arquivo"

    enviar_arquivo(painel, nome, conteudo)

    assert len(api.chamadas) == 1
    payload = api.chamadas[0][1]["json"]
    assert payload["tipo_arquivo"] == tipo
    assert payload["documento_base64"] == base64.b64encode(conteudo).decode("ascii")
    assert "documento_texto" not in payload


def test_canal_de_origem_escolhido_vai_no_envio(painel, api):
    painel.selectbox(key="canal_envio").set_value("Ambulatorio")

    enviar_texto(painel)

    assert api.chamadas[0][1]["json"]["canal_origem"] == "Ambulatorio"


def test_sucesso_mostra_o_resultado_e_guarda_na_sessao(painel):
    antes = len(painel.session_state.documentos_db)

    enviar_texto(painel)

    assert not painel.exception
    assert any("Triagem processada com sucesso" in m for m in mensagens(painel.success))
    assert len(painel.session_state.documentos_db) == antes + 1
    assert painel.session_state.documentos_db[-1]["triagem"] == TRIAGEM_OK


# ============================================================================
# Validações antes de enviar
# ============================================================================

def test_texto_vazio_nao_envia_e_mantem_as_outras_abas(painel, api):
    enviar_texto(painel, "   ")

    assert api.chamadas == []
    assert any("Insira o texto" in m for m in mensagens(painel.error))
    # Sem st.stop(): a aba de histórico continua sendo desenhada.
    assert any("Armazenamento OCI" in s.value for s in painel.subheader)


def test_arquivo_ausente_nao_envia(painel, api):
    painel.radio(key="modo_envio").set_value(MODO_ARQUIVO).run()
    painel.button(key="btn_processar").click().run()

    assert api.chamadas == []
    assert any("Selecione um arquivo" in m for m in mensagens(painel.error))


def test_arquivo_acima_de_10_mb_nao_e_enviado(painel, api):
    antes = len(painel.session_state.documentos_db)

    enviar_arquivo(painel, "grande.pdf", b"0" * (10 * UM_MEGABYTE + 1))

    assert api.chamadas == []
    assert any("passa de 10 MB" in m for m in mensagens(painel.error))
    assert len(painel.session_state.documentos_db) == antes


def test_arquivo_de_exatamente_10_mb_e_enviado(painel, api):
    enviar_arquivo(painel, "limite.pdf", b"0" * (10 * UM_MEGABYTE))

    assert len(api.chamadas) == 1


# ============================================================================
# Falhas da API: a tela mostra o erro, não simula resultado e não toca na sessão
# ============================================================================

def assert_falha_sem_simulacao(painel, antes):
    assert not painel.exception
    assert mensagens(painel.success) == []
    assert len(painel.session_state.documentos_db) == antes
    assert painel.session_state.historico_auditorias == []


def test_409_orienta_a_gerar_outro_id(painel, api):
    api.resposta = RespostaFalsa(409, {"detail": "Documento com este documento_id já foi recebido."})
    antes = len(painel.session_state.documentos_db)

    enviar_texto(painel)

    assert any("Gerar novo ID" in m for m in mensagens(painel.error))
    assert_falha_sem_simulacao(painel, antes)


def test_422_mostra_o_detalhe_da_api(painel, api):
    detalhe = {"detail": "documento_id inválido: use de 1 a 64 caracteres entre letras, números e hífen."}
    api.resposta = RespostaFalsa(422, detalhe)
    antes = len(painel.session_state.documentos_db)

    enviar_texto(painel)

    assert any("HTTP 422" in m for m in mensagens(painel.error))
    # A aba de histórico também desenha JSONs: procura o detalhe da API entre todos.
    assert json.dumps(detalhe) in [j.value for j in painel.json]
    assert_falha_sem_simulacao(painel, antes)


def test_503_diz_que_o_armazenamento_esta_indisponivel(painel, api):
    api.resposta = RespostaFalsa(503, {"detail": "Falha ao gravar o documento no armazenamento."})
    antes = len(painel.session_state.documentos_db)

    enviar_texto(painel)

    assert any("HTTP 503" in m and "armazenamento" in m for m in mensagens(painel.error))
    assert_falha_sem_simulacao(painel, antes)


def test_outro_codigo_mostra_o_status(painel, api):
    api.resposta = RespostaFalsa(500, texto="Internal Server Error")
    antes = len(painel.session_state.documentos_db)

    enviar_texto(painel)

    assert any("HTTP 500" in m for m in mensagens(painel.error))
    assert_falha_sem_simulacao(painel, antes)


def test_resposta_200_sem_json_e_tratada_como_erro(painel, api):
    api.resposta = RespostaFalsa(200, corpo=None, texto="<html>")
    antes = len(painel.session_state.documentos_db)

    enviar_texto(painel)

    assert any("JSON válido" in m for m in mensagens(painel.error))
    assert_falha_sem_simulacao(painel, antes)


def test_api_fora_do_ar_mostra_o_endereco_e_nao_simula(painel, api):
    api.erro = requests.exceptions.ConnectionError("recusada")
    antes = len(painel.session_state.documentos_db)

    enviar_texto(painel)

    assert any("Não foi possível conectar" in m for m in mensagens(painel.error))
    assert any(f"{URL_DA_API}/api/v1/triagem" in m for m in mensagens(painel.markdown))
    assert_falha_sem_simulacao(painel, antes)


def test_timeout_mostra_a_mensagem_e_nao_simula(painel, api):
    api.erro = requests.exceptions.ReadTimeout("lenta")
    antes = len(painel.session_state.documentos_db)

    enviar_texto(painel)

    assert any("demorou demais" in m for m in mensagens(painel.error))
    assert_falha_sem_simulacao(painel, antes)


def test_erro_generico_de_rede_mostra_so_o_tipo_da_excecao(painel, api):
    api.erro = requests.exceptions.InvalidURL("http://segredo")
    antes = len(painel.session_state.documentos_db)

    enviar_texto(painel)

    erros = mensagens(painel.error)
    assert any("InvalidURL" in m for m in erros)
    assert not any("segredo" in m for m in erros)
    assert_falha_sem_simulacao(painel, antes)


# ============================================================================
# A interface não roda o grafo
# ============================================================================

def modulos_importados(caminho):
    arvore = ast.parse(Path(caminho).read_text(encoding="utf-8"))
    nomes = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            nomes.update(alias.name.split(".")[0] for alias in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module:
            nomes.add(no.module.split(".")[0])
    return nomes


def test_interface_nao_importa_o_grafo_nem_as_regras():
    importados = modulos_importados(CAMINHO_DO_PAINEL)

    assert "agente" not in importados
    assert "regras" not in importados


def test_pagina_separada_do_guilherme_nao_existe_mais_na_raiz():
    assert not (RAIZ / "1_Envio_de_Arquivos.py").exists()
