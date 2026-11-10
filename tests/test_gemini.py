"""
MediFlow - Testes da chamada ao Gemini (modelo, novas tentativas e log)
Hackathon ONE G10 - Grupo 10: MediFlow

Responsável: Ingrid Soares
Mapeamento: tests/test_gemini.py
Nenhum teste chama o Gemini de verdade. Dois níveis:
  - lógica das novas tentativas, com um cliente falso (rápido e exato);
  - SDK google-genai de verdade (httpx, timeout, classes de erro) apontado para um servidor
    HTTP local que responde o que o teste roteirizar.
Execução (a partir da raiz): python -m pytest tests/test_gemini.py -v
"""

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors as erros_genai
from pydantic import ValidationError

import agente.visao_multimodal as vm
from agente.leitor_documentos import ConteudoDocumentoIngerido
from agente.visao_multimodal import MODELO_GEMINI_DEFAULT, resolver_modelo

MODELO_DE_TESTE = "modelo-de-teste"
ESPECIALIDADE_DO_GEMINI = "Especialidade-Vinda-Do-Gemini-Falso"
SEGREDO = "SEGREDO-DO-PACIENTE-NAO-PODE-IR-PARA-O-LOG"
TEXTO = "LABORATORIO CENTRAL - LAUDO DE EXAME LABORATORIAL.\nPaciente: Maria Exemplo, 40 anos."


def conteudo_de_texto() -> ConteudoDocumentoIngerido:
    return ConteudoDocumentoIngerido(
        documento_id="DOC-GEMINI",
        formato_normalizado="TXT",
        mime_type="text/plain",
        texto_extraido=TEXTO,
        requer_visao_multimodal=False,
    )


def json_do_gemini() -> str:
    """O texto JSON que o modelo devolveria, no formato de ResultadoExtracaoLLM."""
    return json.dumps(
        {
            "classificacao": {
                "tipo_documento": "Laudo de Exame Laboratorial",
                "especialidade": ESPECIALIDADE_DO_GEMINI,
                "nivel_prioridade": "Rotina",
                "score_confianca_classificacao": 0.97,
            },
            "dados_extraidos": {"paciente": {"nome": "Maria Exemplo", "idade": 40}},
            "texto_transcrito_ocr": "",
        }
    )


def usou_o_gemini(classificacao) -> bool:
    return classificacao.especialidade == ESPECIALIDADE_DO_GEMINI


@pytest.fixture(autouse=True)
def chave_de_teste(monkeypatch):
    # O conftest esvazia as chaves; estes testes precisam do caminho do Gemini, que é todo falso.
    monkeypatch.setenv("LLM_API_KEY", "chave-de-teste")


@pytest.fixture
def esperas(monkeypatch):
    """Substitui o sleep: registra as esperas pedidas, sem esperar de verdade."""
    registradas = []
    monkeypatch.setattr(vm, "_dormir", registradas.append)
    return registradas


def registros_de_aviso(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "agente.visao_multimodal" and r.levelno == logging.WARNING]


# ============================================================================
# Modelo padrão
# ============================================================================

def test_o_modelo_padrao_e_o_gemini_3_5_flash_lite():
    assert MODELO_GEMINI_DEFAULT == "gemini-3.5-flash-lite"


@pytest.mark.parametrize(
    "valor_do_ambiente, esperado",
    [
        (None, MODELO_GEMINI_DEFAULT),
        ("", MODELO_GEMINI_DEFAULT),
        ("   ", MODELO_GEMINI_DEFAULT),
        ("gemini-outro", "gemini-outro"),
        ("  gemini-outro  ", "gemini-outro"),
    ],
)
def test_resolver_modelo_usa_o_ambiente_ou_o_padrao(valor_do_ambiente, esperado):
    assert resolver_modelo(valor_do_ambiente) == esperado


# ============================================================================
# Novas tentativas: lógica, com um cliente falso
# ============================================================================

def erro_http(codigo, mensagem="detalhe do erro"):
    classe = erros_genai.ServerError if codigo >= 500 else erros_genai.ClientError
    return classe(codigo, {"error": {"code": codigo, "message": mensagem, "status": "ERRO"}})


class ClienteFalso:
    """Devolve, na ordem, o que foi roteirizado: uma exceção é levantada, o resto é a resposta."""

    def __init__(self, roteiro):
        self.roteiro = list(roteiro)
        self.chamadas = 0
        self.models = SimpleNamespace(generate_content=self._gerar)

    def _gerar(self, **kwargs):
        self.chamadas += 1
        item = self.roteiro.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def sucesso():
    return SimpleNamespace(parsed=vm.ResultadoExtracaoLLM.model_validate_json(json_do_gemini()), text=json_do_gemini())


@pytest.mark.parametrize("codigo", [500, 503, 504])
def test_falha_transitoria_e_repetida_ate_dar_certo(esperas, codigo):
    cliente = ClienteFalso([erro_http(codigo), erro_http(codigo), sucesso()])

    resposta = vm._gerar_conteudo_com_tentativas(cliente, MODELO_DE_TESTE, [], None)

    assert resposta.parsed.classificacao.especialidade == ESPECIALIDADE_DO_GEMINI
    assert cliente.chamadas == 3
    assert esperas == [2, 4]


def test_timeout_de_rede_e_repetido(esperas):
    cliente = ClienteFalso([httpx.ReadTimeout("lenta"), sucesso()])

    vm._gerar_conteudo_com_tentativas(cliente, MODELO_DE_TESTE, [], None)

    assert cliente.chamadas == 2
    assert esperas == [2]


def test_depois_da_terceira_falha_a_ultima_e_relancada_sem_nova_espera(esperas):
    cliente = ClienteFalso([erro_http(503), erro_http(503), erro_http(503)])

    with pytest.raises(erros_genai.ServerError):
        vm._gerar_conteudo_com_tentativas(cliente, MODELO_DE_TESTE, [], None)

    assert cliente.chamadas == 3  # no máximo 3 chamadas
    assert esperas == [2, 4]  # sem espera depois da última


@pytest.mark.parametrize("codigo", [429, 400, 401, 403, 404])
def test_cota_e_erros_4xx_nao_sao_repetidos(esperas, codigo):
    cliente = ClienteFalso([erro_http(codigo), sucesso()])

    with pytest.raises(erros_genai.ClientError):
        vm._gerar_conteudo_com_tentativas(cliente, MODELO_DE_TESTE, [], None)

    assert cliente.chamadas == 1
    assert esperas == []


def test_excecao_que_nao_e_de_rede_nao_e_repetida(esperas):
    cliente = ClienteFalso([ValueError("bug"), sucesso()])

    with pytest.raises(ValueError):
        vm._gerar_conteudo_com_tentativas(cliente, MODELO_DE_TESTE, [], None)

    assert cliente.chamadas == 1


def test_o_laco_de_novas_tentativas_respeita_o_limite_da_constante():
    assert vm.TENTATIVAS_MAXIMAS == 3
    assert vm.ESPERAS_ENTRE_TENTATIVAS_SEGUNDOS == (2, 4)
    assert len(vm.ESPERAS_ENTRE_TENTATIVAS_SEGUNDOS) == vm.TENTATIVAS_MAXIMAS - 1
    # O pior caso (3 chamadas no prazo máximo mais as esperas) cabe nos 90 s do painel.
    pior_caso = 3 * vm.TIMEOUT_CHAMADA_MS / 1000 + sum(vm.ESPERAS_ENTRE_TENTATIVAS_SEGUNDOS)
    assert pior_caso < 90


# ============================================================================
# Log sem dados do documento
# ============================================================================

@pytest.fixture
def cliente_do_sdk_falso(monkeypatch):
    """Troca genai.Client por um falso que devolve o roteiro do teste e guarda as opções HTTP."""
    criados = []

    def fabrica(roteiro):
        def construir(api_key=None, http_options=None, **kwargs):
            criados.append(SimpleNamespace(api_key=api_key, http_options=http_options))
            return ClienteFalso(roteiro)

        monkeypatch.setattr(vm, "genai", SimpleNamespace(Client=construir))
        return criados

    return fabrica


def test_aviso_de_falha_traz_so_modelo_tipo_e_codigo_http(cliente_do_sdk_falso, esperas, caplog):
    cliente_do_sdk_falso([erro_http(503, SEGREDO)] * 3)

    with caplog.at_level(logging.DEBUG):
        classificacao, _, _ = vm.analisar_documento_com_gemini(conteudo_de_texto(), "Ambulatorio", modelo=MODELO_DE_TESTE)

    assert not usou_o_gemini(classificacao)  # seguiu para o fallback
    avisos = registros_de_aviso(caplog)
    assert len(avisos) == 3  # 2 novas tentativas e a falha final
    assert SEGREDO not in caplog.text
    assert all(MODELO_DE_TESTE in a and "ServerError, HTTP 503" in a for a in avisos)
    assert "Ativando fallback" in avisos[-1]


def test_validation_error_da_resposta_nao_leva_trechos_do_modelo_para_o_log(cliente_do_sdk_falso, esperas, caplog):
    # O modelo devolve um nível de prioridade inválido que carrega o dado do paciente: o Pydantic
    # repete o valor recusado na mensagem do ValidationError. Não é falha transitória: uma chamada só.
    invalido = json.dumps(
        {
            "classificacao": {
                "tipo_documento": "Laudo",
                "nivel_prioridade": SEGREDO,
                "score_confianca_classificacao": 0.9,
            },
            "dados_extraidos": {},
        }
    )
    with pytest.raises(ValidationError) as excecao:
        vm.ResultadoExtracaoLLM.model_validate_json(invalido)
    assert SEGREDO in str(excecao.value), "o teste só vale se a mensagem do ValidationError trouxer o segredo"

    criados = cliente_do_sdk_falso([SimpleNamespace(parsed=None, text=invalido)])

    with caplog.at_level(logging.DEBUG):
        classificacao, _, _ = vm.analisar_documento_com_gemini(conteudo_de_texto(), "Ambulatorio", modelo=MODELO_DE_TESTE)

    assert not usou_o_gemini(classificacao)
    avisos = registros_de_aviso(caplog)
    assert len(avisos) == 1
    assert "ValidationError" in avisos[0]
    assert SEGREDO not in caplog.text
    assert esperas == []
    assert len(criados) == 1


def test_o_cliente_e_criado_com_prazo_por_chamada(cliente_do_sdk_falso, esperas):
    criados = cliente_do_sdk_falso([sucesso()])

    classificacao, _, _ = vm.analisar_documento_com_gemini(conteudo_de_texto(), "Ambulatorio", modelo=MODELO_DE_TESTE)

    assert usou_o_gemini(classificacao)
    assert criados[0].http_options.timeout == vm.TIMEOUT_CHAMADA_MS == 25_000


# ============================================================================
# SDK de verdade (httpx, timeout, classes de erro) contra um servidor HTTP local
# ============================================================================

class ServidorGeminiFalso:
    """Responde, na ordem, o roteiro: (status, corpo, atraso em segundos). Repete o último item."""

    def __init__(self, roteiro):
        self.roteiro = list(roteiro)
        self.caminhos = []
        self._trava = threading.Lock()
        dono = self

        class Tratador(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                with dono._trava:
                    dono.caminhos.append(self.path)
                    indice = min(len(dono.caminhos), len(dono.roteiro)) - 1
                    status, corpo, atraso = dono.roteiro[indice]
                time.sleep(atraso)
                dados = corpo if isinstance(corpo, bytes) else json.dumps(corpo).encode("utf-8")
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(dados)))
                    self.end_headers()
                    self.wfile.write(dados)
                except OSError:
                    pass  # o cliente desistiu (timeout): é o que o teste provoca

            def log_message(self, *args):
                pass

        self._http = ThreadingHTTPServer(("127.0.0.1", 0), Tratador)
        self._http.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._http.server_address[1]}/"
        threading.Thread(target=self._http.serve_forever, daemon=True).start()

    def parar(self):
        self._http.shutdown()
        self._http.server_close()


def resposta_ok():
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": json_do_gemini()}]}, "finishReason": "STOP"}]}


def corpo_de_erro(codigo, status, mensagem):
    return {"error": {"code": codigo, "message": mensagem, "status": status}}


@pytest.fixture
def servidor(monkeypatch):
    """Sobe o servidor falso e aponta o genai.Client de verdade para ele."""
    subidos = []
    cliente_real = vm.genai.Client

    def subir(roteiro, timeout_ms=None):
        falso = ServidorGeminiFalso(roteiro)
        subidos.append(falso)

        def apontado(api_key=None, http_options=None, **kwargs):
            atualizacao = {"base_url": falso.url}
            if timeout_ms is not None:
                atualizacao["timeout"] = timeout_ms
            return cliente_real(api_key=api_key, http_options=http_options.model_copy(update=atualizacao))

        monkeypatch.setattr(vm, "genai", SimpleNamespace(Client=apontado))
        return falso

    yield subir
    for falso in subidos:
        falso.parar()


def test_sdk_real_503_duas_vezes_e_depois_sucesso_usa_o_gemini(servidor, esperas, caplog):
    falso = servidor(
        [
            (503, corpo_de_erro(503, "UNAVAILABLE", SEGREDO), 0),
            (503, corpo_de_erro(503, "UNAVAILABLE", SEGREDO), 0),
            (200, resposta_ok(), 0),
        ]
    )

    with caplog.at_level(logging.DEBUG):
        classificacao, _, _ = vm.analisar_documento_com_gemini(conteudo_de_texto(), "Ambulatorio", modelo=MODELO_DE_TESTE)

    assert usou_o_gemini(classificacao)
    assert len(falso.caminhos) == 3
    assert all(MODELO_DE_TESTE in caminho for caminho in falso.caminhos)
    assert esperas == [2, 4]
    assert SEGREDO not in caplog.text


def test_sdk_real_503_sempre_para_na_terceira_chamada_e_cai_no_fallback(servidor, esperas, caplog):
    falso = servidor([(503, corpo_de_erro(503, "UNAVAILABLE", SEGREDO), 0)])

    with caplog.at_level(logging.DEBUG):
        classificacao, _, _ = vm.analisar_documento_com_gemini(conteudo_de_texto(), "Ambulatorio", modelo=MODELO_DE_TESTE)

    assert not usou_o_gemini(classificacao)
    assert len(falso.caminhos) == 3
    assert esperas == [2, 4]
    assert SEGREDO not in caplog.text


@pytest.mark.parametrize("codigo, status", [(429, "RESOURCE_EXHAUSTED"), (400, "INVALID_ARGUMENT"), (404, "NOT_FOUND")])
def test_sdk_real_cota_e_4xx_chamam_uma_vez_so(servidor, esperas, caplog, codigo, status):
    falso = servidor([(codigo, corpo_de_erro(codigo, status, SEGREDO), 0)])

    with caplog.at_level(logging.DEBUG):
        classificacao, _, _ = vm.analisar_documento_com_gemini(conteudo_de_texto(), "Ambulatorio", modelo=MODELO_DE_TESTE)

    assert not usou_o_gemini(classificacao)
    assert len(falso.caminhos) == 1
    assert esperas == []
    assert f"ClientError, HTTP {codigo}" in caplog.text
    assert SEGREDO not in caplog.text


def test_sdk_real_servidor_que_nao_responde_estoura_o_prazo_e_e_repetido(servidor, esperas, caplog):
    # Prazo de 200 ms por chamada; o servidor demora 1 s. O SDK precisa levantar timeout, e o laço repetir.
    falso = servidor([(200, resposta_ok(), 1.0)], timeout_ms=200)

    with caplog.at_level(logging.DEBUG):
        inicio = time.monotonic()
        classificacao, _, _ = vm.analisar_documento_com_gemini(conteudo_de_texto(), "Ambulatorio", modelo=MODELO_DE_TESTE)
        duracao = time.monotonic() - inicio

    assert not usou_o_gemini(classificacao)
    assert len(falso.caminhos) == 3
    assert esperas == [2, 4]
    assert duracao < 3  # 3 chamadas de ~0,2 s: nenhuma ficou presa esperando o servidor
    assert "ReadTimeout" in caplog.text
