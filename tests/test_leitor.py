"""
MediFlow - Testes do leitor de documentos: arquivo que não abre não vai para o Gemini
Hackathon ONE G10 - Grupo 10: MediFlow

Responsável: Ingrid Soares
Mapeamento: tests/test_leitor.py
A API já barra o arquivo inválido com 422 antes do grafo. Aqui está a segunda barreira: se um arquivo
que não abre chegar ao leitor (o grafo usado direto, por exemplo), ele NUNCA segue para o Gemini e o
documento vai para a revisão humana com a explicação.
Execução (a partir da raiz): python -m pytest tests/test_leitor.py -v
"""

import base64
import io
from types import SimpleNamespace

import pytest
from PIL import Image
from pypdf import PdfWriter

import agente.visao_multimodal as vm
from agente import executar_triagem_mediflow, ingerir_documento_clinico
from contratos.modelos import DestinoRoteamento, DocumentoEntrada, StatusProcessamento, TipoDocumento

SEGREDO = "SEGREDO-DO-PACIENTE-DENTRO-DO-ARQUIVO"


def em_base64(conteudo: bytes) -> str:
    return base64.b64encode(conteudo).decode("ascii")


def png_valido() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), color=(255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def pdf_sem_paginas() -> bytes:
    buffer = io.BytesIO()
    PdfWriter().write(buffer)
    return buffer.getvalue()


def entrada(tipo_arquivo: str, conteudo_base64: str, documento_id="DOC-LEITOR") -> DocumentoEntrada:
    return DocumentoEntrada(documento_id=documento_id, tipo_arquivo=tipo_arquivo, documento_base64=conteudo_base64)


# (rótulo, tipo_arquivo, base64, trecho esperado no motivo)
ARQUIVOS_QUE_NAO_ABREM = [
    ("png-falso", "PNG", em_base64(f"isto nao e uma imagem {SEGREDO}".encode()), "imagem (PNG)"),
    ("jpg-falso", "JPG", em_base64(f"isto nao e uma imagem {SEGREDO}".encode()), "imagem (JPG)"),
    ("pdf-falso", "PDF", em_base64(f"isto nao e um pdf {SEGREDO}".encode()), "PDF não pôde ser aberto"),
    ("pdf-truncado", "PDF", em_base64(b"%PDF-1.4\n" + SEGREDO.encode() + b"\x00" * 30), "PDF não pôde ser aberto"),
    ("pdf-sem-paginas", "PDF", em_base64(pdf_sem_paginas()), "não tem páginas"),
    ("base64-corrompido", "PNG", em_base64(png_valido())[:-3] + "!!!", "Base64 do arquivo é inválido"),
    ("tipo-fora-da-lista", "IMAGEM", em_base64(png_valido()), "tipo de arquivo informado não é aceito"),
]
IDS = [caso[0] for caso in ARQUIVOS_QUE_NAO_ABREM]


@pytest.fixture
def gemini_que_conta_clientes(monkeypatch):
    """Com chave configurada, o caminho do Gemini estaria aberto: o teste conta quantos clientes são criados."""
    monkeypatch.setenv("LLM_API_KEY", "chave-de-teste")
    criados = []

    def construir(**kwargs):
        criados.append(kwargs)
        # Falha não transitória: se o cliente for criado, o documento cai no fallback sem esperar.
        return SimpleNamespace(models=SimpleNamespace(generate_content=lambda **k: (_ for _ in ()).throw(ValueError("sem rede"))))

    monkeypatch.setattr(vm, "genai", SimpleNamespace(Client=construir))
    return criados


# ============================================================================
# Leitor
# ============================================================================

@pytest.mark.parametrize("rotulo, tipo, conteudo_base64, trecho", ARQUIVOS_QUE_NAO_ABREM, ids=IDS)
def test_arquivo_que_nao_abre_volta_marcado_e_sem_bytes_de_midia(rotulo, tipo, conteudo_base64, trecho):
    conteudo = ingerir_documento_clinico(entrada(tipo, conteudo_base64))

    assert trecho in conteudo.falha_leitura
    assert conteudo.bytes_midia is None
    assert conteudo.requer_visao_multimodal is False
    assert conteudo.texto_extraido == ""


@pytest.mark.parametrize("rotulo, tipo, conteudo_base64, trecho", ARQUIVOS_QUE_NAO_ABREM, ids=IDS)
def test_o_motivo_da_falha_nao_repete_trechos_do_arquivo(rotulo, tipo, conteudo_base64, trecho):
    conteudo = ingerir_documento_clinico(entrada(tipo, conteudo_base64))

    assert SEGREDO not in conteudo.falha_leitura
    assert SEGREDO not in conteudo.metadados_leitura


def test_arquivo_valido_nao_tem_falha_de_leitura():
    conteudo = ingerir_documento_clinico(entrada("PNG", em_base64(png_valido())))

    assert conteudo.falha_leitura == ""
    assert conteudo.bytes_midia is not None
    assert conteudo.requer_visao_multimodal is True


# ============================================================================
# O grafo: nunca chama o Gemini e manda para a revisão humana com a explicação
# ============================================================================

@pytest.mark.parametrize("rotulo, tipo, conteudo_base64, trecho", ARQUIVOS_QUE_NAO_ABREM, ids=IDS)
def test_arquivo_que_nao_abre_nunca_vai_para_o_gemini(gemini_que_conta_clientes, rotulo, tipo, conteudo_base64, trecho):
    resposta = executar_triagem_mediflow(entrada(tipo, conteudo_base64))

    assert gemini_que_conta_clientes == []  # nenhum cliente do Gemini chegou a ser criado
    assert resposta.status == StatusProcessamento.PENDENTE_AUDITORIA
    assert resposta.decisao_roteamento.destino_principal == DestinoRoteamento.FILA_REVISAO_HUMANA
    assert resposta.decisao_roteamento.requer_auditoria_humana is True
    assert resposta.decisao_roteamento.notificacao_gerada is None
    assert trecho in resposta.decisao_roteamento.justificativa_roteamento
    assert "revisão humana" in resposta.decisao_roteamento.justificativa_roteamento
    assert resposta.classificacao.tipo_documento == TipoDocumento.OUTRO_DESCONHECIDO.value
    assert resposta.classificacao.score_confianca_classificacao == 0.0
    assert SEGREDO not in resposta.model_dump_json()


def test_arquivo_valido_continua_indo_para_o_gemini(gemini_que_conta_clientes):
    # Garante que o teste acima não passa por acaso: com um arquivo que abre, o cliente é criado.
    executar_triagem_mediflow(entrada("PNG", em_base64(png_valido())))

    assert len(gemini_que_conta_clientes) == 1
