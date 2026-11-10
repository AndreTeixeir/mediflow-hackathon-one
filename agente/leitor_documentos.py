"""
MediFlow - Pipeline de Ingestão e Leitura de Documentos Clínicos (PDF / Imagem / Base64 / Texto)
Hackathon ONE G10 - Grupo 10: MediFlow

Responsável: Ingrid Soares
Mapeamento: agente/leitor_documentos.py
"""

import base64
import io
import json
from dataclasses import dataclass
from typing import Optional, Tuple

from pypdf import PdfReader
from PIL import Image

from contratos.modelos import DocumentoEntrada


MIME_TYPES_POR_FORMATO = {
    "PDF": "application/pdf",
    "PNG": "image/png",
    "JPG": "image/jpeg",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
    "TXT": "text/plain",
    "JSON": "application/json",
}


@dataclass
class ConteudoDocumentoIngerido:
    """
    Resultado padronizado do pipeline de leitura documental antes da etapa de IA/LLM.
    """
    documento_id: str
    formato_normalizado: str
    mime_type: str
    texto_extraido: str
    bytes_midia: Optional[bytes] = None
    requer_visao_multimodal: bool = False
    metadados_leitura: str = ""
    # Preenchido quando o arquivo enviado não abriu (Base64 inválido, PDF ou imagem corrompido). Explica o
    # motivo para a revisão humana; o documento nunca segue para o modelo de visão.
    falha_leitura: str = ""


def normalizar_formato(tipo_arquivo: Optional[str]) -> str:
    """Formato em maiúsculas, sem espaços nem ponto inicial. Vazio vale TXT (texto puro)."""
    return (tipo_arquivo or "").strip().upper().lstrip(".") or "TXT"


def decodificar_base64_seguro(conteudo_b64: str) -> bytes:
    """
    Decodifica uma string Base64 removendo eventual prefixo Data URI (ex.: data:image/jpeg;base64,...).
    É estrita: caractere fora do alfabeto Base64 levanta binascii.Error (um ValueError). Só
    espaços e quebras de linha são ignorados.
    """
    if not conteudo_b64 or not conteudo_b64.strip():
        raise ValueError("Conteúdo Base64 vazio.")

    limpo = conteudo_b64.strip()
    if "," in limpo and limpo.lower().startswith("data:"):
        limpo = limpo.split(",", 1)[1]

    return base64.b64decode("".join(limpo.split()), validate=True)


def extrair_texto_pdf_bytes(pdf_bytes: bytes) -> Tuple[str, int]:
    """
    Extrai texto nativo de um arquivo PDF em memória usando pypdf.
    Retorna (texto_concatenado, numero_paginas).
    """
    leitor = PdfReader(io.BytesIO(pdf_bytes))
    paginas_texto = []
    for pagina in leitor.pages:
        texto_pag = pagina.extract_text() or ""
        if texto_pag.strip():
            paginas_texto.append(texto_pag.strip())

    return "\n\n".join(paginas_texto).strip(), len(leitor.pages)


def normalizar_imagem_bytes(img_bytes: bytes) -> Tuple[bytes, str, Tuple[int, int]]:
    """
    Valida e normaliza bytes de imagem (fotos de receitas, laudos escaneados) via Pillow.
    Retorna (bytes_normalizados, mime_type, dimensoes).
    """
    with Image.open(io.BytesIO(img_bytes)) as img:
        dimensoes = img.size
        formato_img = (img.format or "PNG").upper()
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
            formato_img = "JPEG"

        buffer = io.BytesIO()
        formato_salvar = "JPEG" if formato_img in ("JPG", "JPEG") else "PNG"
        img.save(buffer, format=formato_salvar)
        mime = "image/jpeg" if formato_salvar == "JPEG" else "image/png"
        return buffer.getvalue(), mime, dimensoes


def _conteudo_com_falha(entrada: DocumentoEntrada, formato: str, mime_type: str, motivo: str) -> ConteudoDocumentoIngerido:
    """Conteúdo de um arquivo que não abriu: sem bytes de mídia e sem visão multimodal (ver `falha_leitura`)."""
    return ConteudoDocumentoIngerido(
        documento_id=entrada.documento_id,
        formato_normalizado=formato,
        mime_type=mime_type,
        texto_extraido="",
        bytes_midia=None,
        requer_visao_multimodal=False,
        metadados_leitura=motivo,
        falha_leitura=motivo,
    )


def ingerir_documento_clinico(entrada: DocumentoEntrada) -> ConteudoDocumentoIngerido:
    """
    Ingere um DocumentoEntrada (texto direto, PDF digital, PDF digitalizado ou imagem em Base64)
    e prepara o conteúdo para o agente de IA / Visão Multimodal.
    """
    formato = normalizar_formato(entrada.tipo_arquivo)
    mime_type = MIME_TYPES_POR_FORMATO.get(formato, "application/octet-stream")
    texto_inicial = (entrada.documento_texto or "").strip()

    # Caso 1: Há payload binário em Base64 (PDF ou Imagem enviada via upload)
    if entrada.documento_base64:
        # Segunda barreira (a API já valida antes): bytes que não abrem como PDF ou imagem NUNCA seguem
        # para o Gemini. O conteúdo volta marcado com `falha_leitura` e o grafo o envia à revisão humana.
        try:
            raw_bytes = decodificar_base64_seguro(entrada.documento_base64)
        except ValueError:
            return _conteudo_com_falha(entrada, formato, mime_type, "O conteúdo Base64 do arquivo é inválido.")

        if formato == "PDF":
            try:
                texto_pdf, num_paginas = extrair_texto_pdf_bytes(raw_bytes)
            except Exception as exc:
                # Só o tipo da exceção: a mensagem do pypdf pode trazer trechos do arquivo.
                motivo = f"O arquivo enviado como PDF não pôde ser aberto ({type(exc).__name__})."
                return _conteudo_com_falha(entrada, "PDF", "application/pdf", motivo)
            if num_paginas == 0:
                return _conteudo_com_falha(entrada, "PDF", "application/pdf", "O PDF enviado não tem páginas.")

            texto_combinado = "\n".join(filter(None, [texto_inicial, texto_pdf])).strip()
            # Se o PDF não possui camada de texto suficiente, trata-se de PDF escaneado/digitalizado
            precisa_visao = len(texto_combinado) < 25
            resumo_meta = f"PDF processado ({num_paginas} página(s)). " + (
                "Encaminhado para Visão Multimodal (PDF digitalizado)." if precisa_visao else "Texto digital extraído nativamente."
            )
            return ConteudoDocumentoIngerido(
                documento_id=entrada.documento_id,
                formato_normalizado="PDF",
                mime_type="application/pdf",
                texto_extraido=texto_combinado,
                bytes_midia=raw_bytes if precisa_visao else None,
                requer_visao_multimodal=precisa_visao,
                metadados_leitura=resumo_meta,
            )

        if formato in ("PNG", "JPG", "JPEG", "WEBP"):
            try:
                img_norm, mime_img, dimensoes = normalizar_imagem_bytes(raw_bytes)
            except Exception as exc:
                motivo = f"O arquivo enviado como imagem ({formato}) não pôde ser aberto ({type(exc).__name__})."
                return _conteudo_com_falha(entrada, formato, mime_type, motivo)
            return ConteudoDocumentoIngerido(
                documento_id=entrada.documento_id,
                formato_normalizado=formato,
                mime_type=mime_img,
                texto_extraido=texto_inicial,
                bytes_midia=img_norm,
                requer_visao_multimodal=True,
                metadados_leitura=f"Imagem clínica ({dimensoes[0]}x{dimensoes[1]}) preparada para OCR / Visão Multimodal.",
            )

        if formato in ("TXT", "JSON"):
            texto_decodificado = raw_bytes.decode("utf-8", errors="replace").strip()
            texto_final = "\n".join(filter(None, [texto_inicial, texto_decodificado])).strip()
            return ConteudoDocumentoIngerido(
                documento_id=entrada.documento_id,
                formato_normalizado=formato,
                mime_type=mime_type,
                texto_extraido=texto_final,
                bytes_midia=None,
                requer_visao_multimodal=False,
                metadados_leitura=f"Arquivo {formato} decodificado de Base64 com sucesso.",
            )

        # Um arquivo em tipo que o leitor não sabe abrir não pode ser ignorado em silêncio.
        return _conteudo_com_falha(
            entrada,
            formato,
            mime_type,
            "O tipo de arquivo informado não é aceito para envio em Base64 (use PDF, PNG, JPG, JPEG, WEBP ou TXT).",
        )

    # Caso 2: Documento recebido diretamente via campo documento_texto (JSON da API ou pós-OCR)
    if formato == "JSON" and texto_inicial.startswith("{"):
        try:
            objeto_json = json.loads(texto_inicial)
            if isinstance(objeto_json, dict) and "documento_texto" in objeto_json:
                texto_inicial = str(objeto_json["documento_texto"])
        except json.JSONDecodeError:
            pass

    return ConteudoDocumentoIngerido(
        documento_id=entrada.documento_id,
        formato_normalizado=formato,
        mime_type=mime_type,
        texto_extraido=texto_inicial,
        bytes_midia=None,
        requer_visao_multimodal=False,
        metadados_leitura="Texto clínico recebido diretamente no payload de entrada.",
    )
