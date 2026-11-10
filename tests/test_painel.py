"""
MediFlow - Testes do painel Streamlit (envio, fila de auditoria, histórico e contadores)
Hackathon ONE G10 - Grupo 10: MediFlow

Mapeamento: tests/test_painel.py
O painel roda no AppTest do Streamlit, no mesmo processo do teste. Os testes são de dois tipos:
  - com a API REAL em processo: o painel fala com a aplicação FastAPI (e uma pasta temporária como
    armazenamento) pelo mesmo cliente HTTP, trocando só o transporte. Prova que a fila, a decisão, o
    histórico e os contadores vêm da API e que a decisão continua lá depois de recarregar a página;
  - com uma API FALSA que devolve o código de erro pedido, para conferir a mensagem de cada falha.
Nenhuma rede, nenhum Gemini, nenhuma OCI.
Execução (a partir da raiz): python -m pytest tests/test_painel.py -v
"""

import ast
import base64
import json
from pathlib import Path

import pytest
import requests
import streamlit as st
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from api.main import app, obter_repositorio
from armazenamento.repositorio import RepositorioLocal

RAIZ = Path(__file__).resolve().parent.parent
CAMINHO_DO_PAINEL = str(RAIZ / "interface" / "app.py")
URL_DA_API = "http://api-de-teste:8000"
MODO_ARQUIVO = "Enviar arquivo PDF ou imagem"
UM_MEGABYTE = 1024 * 1024
LAUDO_SEM_CRM = (
    "LABORATORIO CENTRAL - LAUDO DE EXAME LABORATORIAL.\n"
    "Paciente: Mariana Alcantara de Souza, 34 anos.\n"
    "Solicitante: Dr. Roberto Viana.\n"
    "Exame: Hemograma completo.\n"
    "CONCLUSÃO: Parâmetros dentro dos limites da normalidade."
)

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
    """Substitui requests.request: guarda as chamadas e devolve (ou levanta) o que o teste pediu.

    Sem roteiro, as listas (fila e histórico) vêm vazias e a triagem responde TRIAGEM_OK."""

    def __init__(self):
        self.chamadas = []  # (método, caminho, kwargs)
        self.respostas = {}  # (método, caminho) -> RespostaFalsa ou exceção
        self.padrao = {
            ("GET", "/api/v1/auditoria/pendentes"): RespostaFalsa(200, []),
            ("GET", "/api/v1/documentos"): RespostaFalsa(200, []),
            ("POST", "/api/v1/triagem"): RespostaFalsa(200, TRIAGEM_OK),
        }

    def __call__(self, metodo, url, **kwargs):
        caminho = url.removeprefix(URL_DA_API)
        self.chamadas.append((metodo, caminho, kwargs))
        resposta = self.respostas.get((metodo, caminho), self.padrao.get((metodo, caminho)))
        if resposta is None:
            raise AssertionError(f"chamada sem roteiro: {metodo} {caminho}")
        if isinstance(resposta, Exception):
            raise resposta
        return resposta

    def chamadas_de(self, metodo, caminho):
        return [kwargs for m, c, kwargs in self.chamadas if (m, c) == (metodo, caminho)]

    def falhar_tudo_com(self, resposta):
        for chave in self.padrao:
            self.respostas[chave] = resposta


class ApiReal:
    """A aplicação FastAPI de verdade, em processo, com uma pasta temporária como armazenamento."""

    def __init__(self, repositorio):
        app.dependency_overrides[obter_repositorio] = lambda: repositorio
        self.cliente = TestClient(app)
        self.chamadas = []

    def __call__(self, metodo, url, **kwargs):
        caminho = url.removeprefix(URL_DA_API)
        self.chamadas.append((metodo, caminho, kwargs))
        return self.cliente.request(metodo, caminho, json=kwargs.get("json"))

    def chamadas_de(self, metodo, caminho):
        return [kwargs for m, c, kwargs in self.chamadas if (m, c) == (metodo, caminho)]


@pytest.fixture(autouse=True)
def ambiente_do_painel(monkeypatch):
    # O painel chama load_dotenv(): o .env de quem roda os testes não pode mudar o endereço da API.
    monkeypatch.setenv("MEDIFLOW_API_URL", URL_DA_API)
    # O cache é global ao processo: sem limpar, uma resposta de um teste apareceria no seguinte.
    st.cache_data.clear()
    yield
    st.cache_data.clear()
    app.dependency_overrides.clear()


@pytest.fixture
def api(monkeypatch):
    falsa = ApiFalsa()
    monkeypatch.setattr(requests, "request", falsa)
    return falsa


@pytest.fixture
def api_real(monkeypatch, tmp_path):
    real = ApiReal(RepositorioLocal(tmp_path))
    monkeypatch.setattr(requests, "request", real)
    return real


def abrir_painel():
    painel = AppTest.from_file(CAMINHO_DO_PAINEL, default_timeout=30)
    painel.run()
    assert not painel.exception, [e.value for e in painel.exception]
    return painel


@pytest.fixture
def painel(api):
    return abrir_painel()


def mensagens(elementos):
    return [e.value for e in elementos]


def botao_registrar_decisao(painel):
    return next(b for b in painel.button if b.label == "✅ Registrar decisão")


def fila(painel):
    """Os IDs da fila de auditoria, na ordem em que a tela os lista (o AppTest devolve os rótulos já formatados)."""
    return [rotulo.split(" — ")[0] for rotulo in painel.selectbox(key="caso_em_auditoria").options]


def metricas(painel):
    return {m.label: m.value for m in painel.metric}


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

    [chamada] = api.chamadas_de("POST", "/api/v1/triagem")
    assert chamada["timeout"] == 90
    assert chamada["json"] == {
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

    [chamada] = api.chamadas_de("POST", "/api/v1/triagem")
    assert chamada["json"]["tipo_arquivo"] == tipo
    assert chamada["json"]["documento_base64"] == base64.b64encode(conteudo).decode("ascii")
    assert "documento_texto" not in chamada["json"]


def test_canal_de_origem_escolhido_vai_no_envio(painel, api):
    painel.selectbox(key="canal_envio").set_value("Ambulatorio")

    enviar_texto(painel)

    assert api.chamadas_de("POST", "/api/v1/triagem")[0]["json"]["canal_origem"] == "Ambulatorio"


def test_sucesso_mostra_o_resultado_da_triagem(painel):
    enviar_texto(painel)

    assert not painel.exception
    assert any("Triagem processada com sucesso" in m for m in mensagens(painel.success))
    assert any("Resultado da Triagem" in m for m in mensagens(painel.markdown))


# ============================================================================
# Validações antes de enviar
# ============================================================================

def test_texto_vazio_nao_envia_e_mantem_as_outras_abas(painel, api):
    enviar_texto(painel, "   ")

    assert api.chamadas_de("POST", "/api/v1/triagem") == []
    assert any("Insira o texto" in m for m in mensagens(painel.error))
    # Sem st.stop(): a aba de histórico continua sendo desenhada.
    assert any("Histórico dos Documentos" in s.value for s in painel.subheader)


def test_arquivo_ausente_nao_envia(painel, api):
    painel.radio(key="modo_envio").set_value(MODO_ARQUIVO).run()
    painel.button(key="btn_processar").click().run()

    assert api.chamadas_de("POST", "/api/v1/triagem") == []
    assert any("Selecione um arquivo" in m for m in mensagens(painel.error))


def test_arquivo_acima_de_10_mb_nao_e_enviado(painel, api):
    enviar_arquivo(painel, "grande.pdf", b"0" * (10 * UM_MEGABYTE + 1))

    assert api.chamadas_de("POST", "/api/v1/triagem") == []
    assert any("passa de 10 MB" in m for m in mensagens(painel.error))


def test_arquivo_de_exatamente_10_mb_e_enviado(painel, api):
    enviar_arquivo(painel, "limite.pdf", b"0" * (10 * UM_MEGABYTE))

    assert len(api.chamadas_de("POST", "/api/v1/triagem")) == 1


# ============================================================================
# Falhas do envio: a tela mostra o erro e não simula resultado
# ============================================================================

def assert_envio_sem_simulacao(painel):
    assert not painel.exception
    assert not any("Triagem processada" in m for m in mensagens(painel.success))
    assert not any("Resultado da Triagem" in m for m in mensagens(painel.markdown))
    # O painel não guarda nenhum "banco" de documentos na sessão: só a API tem os documentos.
    assert "documentos_db" not in painel.session_state
    assert "historico_auditorias" not in painel.session_state


def test_409_orienta_a_gerar_outro_id(painel, api):
    api.respostas[("POST", "/api/v1/triagem")] = RespostaFalsa(409, {"detail": "Documento com este documento_id já foi recebido."})

    enviar_texto(painel)

    assert any("Gerar novo ID" in m for m in mensagens(painel.error))
    assert_envio_sem_simulacao(painel)


def test_422_mostra_o_detalhe_da_api(painel, api):
    detalhe = {"detail": "documento_id inválido: use de 1 a 64 caracteres entre letras, números e hífen."}
    api.respostas[("POST", "/api/v1/triagem")] = RespostaFalsa(422, detalhe)

    enviar_texto(painel)

    assert any("HTTP 422" in m for m in mensagens(painel.error))
    assert json.dumps(detalhe) in [j.value for j in painel.json]
    assert_envio_sem_simulacao(painel)


def test_503_diz_que_o_armazenamento_esta_indisponivel(painel, api):
    api.respostas[("POST", "/api/v1/triagem")] = RespostaFalsa(503, {"detail": "Falha ao gravar o documento no armazenamento."})

    enviar_texto(painel)

    assert any("HTTP 503" in m and "armazenamento" in m for m in mensagens(painel.error))
    assert_envio_sem_simulacao(painel)


def test_outro_codigo_mostra_o_status(painel, api):
    api.respostas[("POST", "/api/v1/triagem")] = RespostaFalsa(500, texto="Internal Server Error")

    enviar_texto(painel)

    assert any("HTTP 500" in m for m in mensagens(painel.error))
    assert_envio_sem_simulacao(painel)


def test_resposta_200_sem_json_e_tratada_como_erro(painel, api):
    api.respostas[("POST", "/api/v1/triagem")] = RespostaFalsa(200, corpo=None, texto="<html>")

    enviar_texto(painel)

    assert any("JSON válido" in m for m in mensagens(painel.error))
    assert_envio_sem_simulacao(painel)


def test_api_fora_do_ar_mostra_o_endereco_e_nao_simula(painel, api):
    api.respostas[("POST", "/api/v1/triagem")] = requests.exceptions.ConnectionError("recusada")

    enviar_texto(painel)

    assert any("Não foi possível conectar" in m for m in mensagens(painel.error))
    assert any(f"{URL_DA_API}/api/v1/triagem" in m for m in mensagens(painel.markdown))
    assert_envio_sem_simulacao(painel)


def test_timeout_mostra_a_mensagem_e_nao_simula(painel, api):
    api.respostas[("POST", "/api/v1/triagem")] = requests.exceptions.ReadTimeout("lenta")

    enviar_texto(painel)

    assert any("demorou demais" in m for m in mensagens(painel.error))
    assert_envio_sem_simulacao(painel)


def test_erro_generico_de_rede_mostra_so_o_tipo_da_excecao(painel, api):
    api.respostas[("POST", "/api/v1/triagem")] = requests.exceptions.InvalidURL("http://segredo")

    enviar_texto(painel)

    erros = mensagens(painel.error)
    assert any("InvalidURL" in m for m in erros)
    assert not any("segredo" in m for m in erros)
    assert_envio_sem_simulacao(painel)


# ============================================================================
# Fila de auditoria, histórico e contadores: tudo vem da API
# ============================================================================

def enviar_pela_api(api_real, documento_id, texto=LAUDO_SEM_CRM, canal="Ambulatorio"):
    resposta = api_real.cliente.post(
        "/api/v1/triagem", json={"documento_id": documento_id, "documento_texto": texto, "canal_origem": canal}
    )
    assert resposta.status_code == 200, resposta.text
    return resposta.json()


def enviar_cenario_3(api_real, documento_id):
    cenario = json.loads((RAIZ / "dados_teste" / "3_auditoria_humana" / "exemplo.json").read_text(encoding="utf-8"))
    corpo = api_real.cliente.post("/api/v1/triagem", json=dict(cenario["requisicao_entrada"], documento_id=documento_id))
    assert corpo.status_code == 200 and corpo.json()["status"] == "pendente_auditoria"
    return corpo.json()


def test_fila_vazia_mostra_a_mensagem_de_fila_vazia(api_real):
    painel = abrir_painel()

    assert any("Não há documentos pendentes" in m for m in mensagens(painel.success))
    assert metricas(painel)["⚠️ Fila de Revisão Humana"] == "0"


def test_fila_mostra_o_que_a_api_devolveu_sobre_o_documento_retido(api_real):
    pendente = enviar_pela_api(api_real, "DOC-FILA")

    painel = abrir_painel()

    assert fila(painel) == ["DOC-FILA"]
    texto = " ".join(mensagens(painel.markdown))
    assert "DOC-FILA" in texto and "Laudo de Exame Laboratorial" in texto
    assert any(pendente["decisao_roteamento"]["justificativa_roteamento"] in m for m in mensagens(painel.warning))
    assert api_real.chamadas_de("GET", "/api/v1/auditoria/pendentes")


def test_campos_de_dados_extraidos_ficam_visiveis_preenchidos_e_desabilitados(api_real):
    enviar_pela_api(api_real, "DOC-DADOS")

    painel = abrir_painel()

    campos = {c.label: c for c in painel.text_input if c.key and c.key.endswith("_DOC-DADOS")}
    assert set(campos) == {
        "Nome do Paciente:", "Idade:", "Médico Solicitante:", "CRM com UF:", "Diagnóstico Principal:", "CID-10 Sugerido:",
    }
    assert all(c.disabled for c in campos.values())
    assert campos["Nome do Paciente:"].value == "Mariana Alcantara de Souza"
    assert campos["Idade:"].value == "34"
    assert campos["Médico Solicitante:"].value == "Dr. Roberto Viana"
    assert any("Correção de dados será habilitada numa próxima versão" in m for m in mensagens(painel.warning))


def test_campos_desabilitados_nao_mudam_o_que_e_enviado_na_decisao(api_real):
    enviar_pela_api(api_real, "DOC-NADA-CORRIGE")
    painel = abrir_painel()
    painel.text_input(key="nome_do_revisor").set_value("Lethicia")
    painel.text_area(key="justificativa_DOC-NADA-CORRIGE").set_value("Dados conferidos com o original.")

    botao_registrar_decisao(painel).click().run()

    [chamada] = api_real.chamadas_de("POST", "/api/v1/auditoria/DOC-NADA-CORRIGE/decisao")
    assert sorted(chamada["json"]) == ["destino", "justificativa", "revisor"]  # só a decisão: nenhum dado corrigido


def test_nada_e_lido_de_arquivos_de_exemplo_nem_do_grafo():
    codigo = Path(CAMINHO_DO_PAINEL).read_text(encoding="utf-8")
    arvore = ast.parse(codigo)
    importados = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            importados.update(alias.name.split(".")[0] for alias in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module:
            importados.add(no.module.split(".")[0])

    assert "dados_teste" not in codigo
    assert "use_container_width" not in codigo  # Streamlit 1.66: width="stretch"
    assert not {"agente", "regras", "armazenamento", "api"} & importados


def test_com_a_fila_cheia_os_dados_de_exemplo_nao_aparecem(api_real):
    # Sem nenhum documento enviado, nada que esteja nos arquivos de exemplo pode aparecer na tela.
    painel = abrir_painel()

    texto = " ".join(mensagens(painel.markdown) + mensagens(painel.success) + mensagens(painel.info))
    assert "DOC-CLIN-2026" not in texto
    assert metricas(painel)["📄 Total de Documentos"] == "0"


# ----------------------------------------------------------------------------
# Decisão pela API (rotina, urgente, rejeitar) e o que fica depois de recarregar
# ----------------------------------------------------------------------------

def decidir_no_painel(painel, documento_id, destino="Rotina", revisor="Lethicia", justificativa="Dados conferidos com o original."):
    caso = painel.selectbox(key="caso_em_auditoria")
    if caso.value != documento_id:  # o formulário é do caso selecionado
        caso.set_value(documento_id).run()
    painel.radio(key=f"destino_{documento_id}").set_value(destino)
    painel.text_input(key="nome_do_revisor").set_value(revisor)
    painel.text_area(key=f"justificativa_{documento_id}").set_value(justificativa)
    botao_registrar_decisao(painel).click().run()
    return painel


@pytest.mark.parametrize(
    "opcao, destino_da_api, destino_final, bucket",
    [
        ("Rotina", "rotina", "Prontuario_Eletronico_do_Paciente", "mediflow-rotina"),
        ("Urgente", "urgente", "Fila_Emergencia_Medica", "mediflow-urgentes"),
        ("Rejeitar", "rejeitado", "Documento_Rejeitado", "mediflow-rejeitados"),
    ],
)
def test_decisao_pelas_tres_opcoes_chega_a_api_e_recarrega_a_fila(api_real, opcao, destino_da_api, destino_final, bucket):
    enviar_pela_api(api_real, "DOC-DECIDE")
    painel = abrir_painel()
    assert fila(painel) == ["DOC-DECIDE"]
    assert [r.options for r in painel.radio if r.key == "destino_DOC-DECIDE"] == [["Rotina", "Urgente", "Rejeitar"]]

    decidir_no_painel(painel, "DOC-DECIDE", destino=opcao, revisor="  Lethicia  ", justificativa="  Dados conferidos com o original.  ")

    assert not painel.exception
    [chamada] = api_real.chamadas_de("POST", "/api/v1/auditoria/DOC-DECIDE/decisao")
    assert chamada["json"] == {"destino": destino_da_api, "revisor": "Lethicia", "justificativa": "Dados conferidos com o original."}
    assert any(destino_final in m and bucket in m and "ficou registrada" in m for m in mensagens(painel.success))
    # A fila foi relida pela API: o documento saiu dela.
    assert any("Não há documentos pendentes" in m for m in mensagens(painel.success))
    assert metricas(painel)["⚠️ Fila de Revisão Humana"] == "0"


def test_a_decisao_continua_la_depois_de_recarregar_a_pagina(api_real):
    enviar_pela_api(api_real, "DOC-PERSISTE")
    painel = abrir_painel()
    decidir_no_painel(painel, "DOC-PERSISTE", destino="Urgente", justificativa="Quadro agudo confirmado.")

    st.cache_data.clear()
    recarregada = abrir_painel()  # uma sessão nova, como recarregar a página

    assert any("Não há documentos pendentes" in m for m in mensagens(recarregada.success))
    metr = metricas(recarregada)
    assert metr["⚠️ Fila de Revisão Humana"] == "0"
    assert metr["🚨 Emergências Médicas"] == "1"
    assert metr["📄 Total de Documentos"] == "1"
    # E o registro da auditoria está no documento, gravado pela API.
    decidido = json.loads(next(Path(api_real.cliente.app.dependency_overrides[obter_repositorio]()._base).rglob("mediflow-urgentes/DOC-PERSISTE.json")).read_text(encoding="utf-8"))
    assert decidido["auditoria_humana"]["revisor"] == "Lethicia"
    assert decidido["auditoria_humana"]["destino_original"] == "Fila_Revisao_Humana"


def test_decisao_sem_nome_de_quem_revisa_nao_chama_a_api(api_real):
    enviar_pela_api(api_real, "DOC-SEM-NOME")
    painel = abrir_painel()

    decidir_no_painel(painel, "DOC-SEM-NOME", revisor="   ")

    assert any("nome de quem revisa" in m for m in mensagens(painel.error))
    assert api_real.chamadas_de("POST", "/api/v1/auditoria/DOC-SEM-NOME/decisao") == []
    assert fila(painel) == ["DOC-SEM-NOME"]  # continua na fila


@pytest.mark.parametrize("justificativa", ["", "    ", "Ok", "  ab  "])
def test_decisao_sem_justificativa_valida_nao_chama_a_api(api_real, justificativa):
    enviar_pela_api(api_real, "DOC-SEM-JUST")
    painel = abrir_painel()

    decidir_no_painel(painel, "DOC-SEM-JUST", justificativa=justificativa)

    assert any("justificativa" in m for m in mensagens(painel.error))
    assert api_real.chamadas_de("POST", "/api/v1/auditoria/DOC-SEM-JUST/decisao") == []


def test_o_nome_de_quem_revisa_vale_para_os_proximos_documentos(api_real):
    enviar_pela_api(api_real, "DOC-A")
    enviar_pela_api(api_real, "DOC-B")
    painel = abrir_painel()

    decidir_no_painel(painel, "DOC-A", revisor="Lethicia")

    assert painel.text_input(key="nome_do_revisor").value == "Lethicia"
    assert fila(painel) == ["DOC-B"]


def test_historico_vem_da_api_com_todos_os_buckets(api_real):
    enviar_pela_api(api_real, "DOC-H-RETIDO")
    enviar_pela_api(api_real, "DOC-H-REJEITADO")
    painel = abrir_painel()
    decidir_no_painel(painel, "DOC-H-REJEITADO", destino="Rejeitar", justificativa="Rasura impede a leitura.")

    painel = abrir_painel()

    [tabela] = painel.dataframe
    linhas = {linha["Documento"]: linha for linha in tabela.value.to_dict("records")}
    assert set(linhas) == {"DOC-H-RETIDO", "DOC-H-REJEITADO"}
    assert linhas["DOC-H-REJEITADO"]["Status"] == "rejeitado"
    assert linhas["DOC-H-REJEITADO"]["Destino"] == "Documento_Rejeitado"
    assert linhas["DOC-H-REJEITADO"]["Bucket"] == "mediflow-rejeitados"
    assert linhas["DOC-H-RETIDO"]["Status"] == "pendente_auditoria"
    assert api_real.chamadas_de("GET", "/api/v1/documentos")


def test_contadores_do_topo_vem_da_api(api_real):
    enviar_cenario_3(api_real, "DOC-C-RETIDO-1")
    enviar_cenario_3(api_real, "DOC-C-RETIDO-2")
    enviar_cenario_3(api_real, "DOC-C-URGENTE")
    enviar_pela_api(api_real, "DOC-C-ROTINA", texto="Aviso interno: a reuniao de equipe foi remarcada para a proxima semana.")
    painel = abrir_painel()
    decidir_no_painel(painel, "DOC-C-URGENTE", destino="Urgente")
    decidir_no_painel(abrir_painel(), "DOC-C-RETIDO-2", destino="Rejeitar", justificativa="Rasura impede a leitura.")

    metr = metricas(abrir_painel())

    assert metr["📄 Total de Documentos"] == "4"
    assert metr["⚠️ Fila de Revisão Humana"] == "2"  # o aviso interno e o RETIDO-1
    assert metr["🚨 Emergências Médicas"] == "1"
    assert metr["🚫 Rejeitados"] == "1"


def test_envio_pela_tela_atualiza_a_fila_e_os_contadores_na_mesma_execucao(api_real):
    painel = abrir_painel()
    assert metricas(painel)["⚠️ Fila de Revisão Humana"] == "0"

    painel.text_input(key="documento_id").set_value("DOC-NOVO-PELA-TELA")
    enviar_texto(painel, LAUDO_SEM_CRM)

    assert not painel.exception
    assert metricas(painel)["⚠️ Fila de Revisão Humana"] == "1"
    assert metricas(painel)["📄 Total de Documentos"] == "1"
    assert fila(painel) == ["DOC-NOVO-PELA-TELA"]


def test_botao_atualizar_a_fila_relê_pela_api(api_real):
    painel = abrir_painel()
    assert metricas(painel)["⚠️ Fila de Revisão Humana"] == "0"
    enviar_pela_api(api_real, "DOC-DE-OUTRA-PESSOA")  # chegou por fora, enquanto a tela estava aberta
    painel.run()
    assert metricas(painel)["⚠️ Fila de Revisão Humana"] == "0"  # o cache de 30 s ainda vale

    painel.button(key="btn_atualizar").click().run()

    assert metricas(painel)["⚠️ Fila de Revisão Humana"] == "1"


def test_decisao_de_documento_que_outra_pessoa_ja_decidiu_atualiza_a_fila_e_avisa(api_real):
    enviar_pela_api(api_real, "DOC-CONCORRIDO")
    painel = abrir_painel()
    # Enquanto esta tela estava aberta, outra pessoa decidiu o mesmo documento.
    api_real.cliente.post(
        "/api/v1/auditoria/DOC-CONCORRIDO/decisao", json={"destino": "rejeitado", "revisor": "Outra", "justificativa": "Rasura impede."}
    )

    decidir_no_painel(painel, "DOC-CONCORRIDO", destino="Urgente")

    assert any("HTTP 404" in m and "fila foi atualizada" in m for m in mensagens(painel.warning))
    assert any("Não há documentos pendentes" in m for m in mensagens(painel.success))
    assert metricas(painel)["⚠️ Fila de Revisão Humana"] == "0"


# ----------------------------------------------------------------------------
# Erros da API na fila, no histórico e na decisão: a mensagem aparece e nada é simulado
# ----------------------------------------------------------------------------

ERROS_DA_API = [
    (401, {"detail": "Chave de acesso ausente ou inválida."}, "HTTP 401", "MEDIFLOW_API_KEY"),
    (404, {"detail": "Documento não encontrado entre os pendentes de auditoria."}, "HTTP 404", "não encontrado entre os pendentes"),
    (409, {"detail": "Já existe um documento com este ID no destino da decisão."}, "HTTP 409", "destino da decisão"),
    (413, {"detail": "Corpo acima de 15 MB."}, "HTTP 413", "15 MB"),
    (422, {"detail": [{"loc": ["body", "revisor"], "msg": "Field required", "type": "missing"}]}, "HTTP 422", None),
    (429, {"detail": "Limite diário de documentos atingido. Tente novamente amanhã."}, "HTTP 429", "Tente novamente amanhã"),
    (503, {"detail": "Falha ao acessar o armazenamento."}, "HTTP 503", "Falha ao acessar o armazenamento"),
]


@pytest.mark.parametrize("status, corpo, esperado_1, esperado_2", ERROS_DA_API)
def test_erro_da_api_na_fila_e_no_historico_mostra_a_mensagem_e_nao_simula(api, status, corpo, esperado_1, esperado_2):
    api.falhar_tudo_com(RespostaFalsa(status, corpo))

    painel = abrir_painel()

    erros = mensagens(painel.error)
    assert any(esperado_1 in m for m in erros)
    if esperado_2:
        assert any(esperado_2 in m for m in erros)
    # Sem dados da API não há fila, histórico nem contadores: nada de exemplo no lugar.
    assert painel.metric == []
    assert any("Contadores indisponíveis" in m for m in mensagens(painel.warning))
    assert mensagens(painel.success) == []
    assert painel.dataframe == []


@pytest.mark.parametrize("status, corpo, esperado_1, esperado_2", ERROS_DA_API)
def test_erro_da_api_na_decisao_mostra_a_mensagem_e_a_decisao_nao_vale(api, status, corpo, esperado_1, esperado_2):
    pendente = {
        "documento_id": "DOC-ERRO", "tipo_documento": "Receituário Médico", "nivel_prioridade": "Prioritário",
        "score_confianca_classificacao": 0.4, "justificativa_roteamento": "Baixa confiança.",
        "dados_extraidos": {}, "data": "2026-11-10T15:30:00Z",
    }
    api.respostas[("GET", "/api/v1/auditoria/pendentes")] = RespostaFalsa(200, [pendente])
    api.respostas[("POST", "/api/v1/auditoria/DOC-ERRO/decisao")] = RespostaFalsa(status, corpo)
    painel = abrir_painel()

    decidir_no_painel(painel, "DOC-ERRO", destino="Urgente")

    assert not painel.exception
    mostradas = mensagens(painel.error) + mensagens(painel.warning)
    assert any(esperado_1 in m for m in mostradas)
    if esperado_2:
        assert any(esperado_2.lower() in m.lower() for m in mostradas)
    assert not any("ficou registrada" in m for m in mensagens(painel.success))  # nenhuma decisão simulada
    if status == 422:
        assert json.dumps(corpo) in [j.value for j in painel.json]


def test_api_fora_do_ar_na_abertura_mostra_o_endereco_e_nao_simula(api):
    api.falhar_tudo_com(requests.exceptions.ConnectionError("recusada"))

    painel = abrir_painel()

    assert any("Não foi possível conectar" in m for m in mensagens(painel.error))
    assert any(f"{URL_DA_API}/api/v1/auditoria/pendentes" in m for m in mensagens(painel.markdown))
    assert painel.metric == []
    assert painel.dataframe == []
    assert mensagens(painel.success) == []


def test_timeout_na_abertura_mostra_a_mensagem(api):
    api.falhar_tudo_com(requests.exceptions.ReadTimeout("lenta"))

    painel = abrir_painel()

    assert any("demorou demais" in m for m in mensagens(painel.error))
    assert painel.metric == []
