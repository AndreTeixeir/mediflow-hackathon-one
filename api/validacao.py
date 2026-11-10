"""
MediFlow - Validação da entrada da triagem
Hackathon ONE G10 - Grupo 10: MediFlow

Mapeamento: api/validacao.py
Roda antes de qualquer gravação e antes de chamar o grafo. Entrada inválida vira 422 com
uma mensagem curta em português, nunca 500, e a mensagem nunca repete bytes nem trechos
do arquivo enviado.
"""

import io
import logging
import re

from fastapi import HTTPException
from PIL import Image
from pypdf import PdfReader

from agente.leitor_documentos import decodificar_base64_seguro, normalizar_formato
from contratos.modelos import DocumentoEntrada

# Ao ler um PDF inválido, o pypdf registra por conta própria trechos do arquivo (por exemplo,
# "invalid pdf header: b'...'", com os primeiros bytes). Conteúdo de documento clínico não vai
# para o log, então o logger dele fica mudo neste processo.
logging.getLogger("pypdf").setLevel(logging.CRITICAL)

# O documento_id vira nome de objeto no bucket: "/" e ".." são proibidos.
PADRAO_DOCUMENTO_ID = re.compile(r"[A-Za-z0-9-]{1,64}")

FORMATOS_PDF = {"PDF"}
FORMATOS_IMAGEM = {"PNG", "JPG", "JPEG", "WEBP"}
# TXT é o formato do texto puro; tipo_arquivo vazio vale TXT.
FORMATOS_ACEITOS_COM_ARQUIVO = FORMATOS_PDF | FORMATOS_IMAGEM | {"TXT"}


def _entrada_invalida(mensagem: str) -> HTTPException:
    return HTTPException(status_code=422, detail=mensagem)


def _validar_pdf(dados: bytes) -> None:
    try:
        leitor = PdfReader(io.BytesIO(dados))
        protegido = leitor.is_encrypted
        # Contar as páginas força a leitura da estrutura do arquivo.
        paginas = 0 if protegido else len(leitor.pages)
    except Exception:
        raise _entrada_invalida("documento_base64 não é um PDF válido.") from None
    if protegido:
        raise _entrada_invalida("documento_base64 é um PDF protegido por senha, que não é aceito.")
    if paginas == 0:
        raise _entrada_invalida("documento_base64 não é um PDF válido.")


def _validar_imagem(dados: bytes) -> None:
    try:
        with Image.open(io.BytesIO(dados)) as imagem:
            imagem.verify()
    except Exception:
        raise _entrada_invalida("documento_base64 não é uma imagem válida.") from None


def _validar_arquivo(tipo_arquivo: str, conteudo_base64: str) -> None:
    formato = normalizar_formato(tipo_arquivo)
    if formato not in FORMATOS_ACEITOS_COM_ARQUIVO:
        raise _entrada_invalida("tipo_arquivo não aceito: use PDF, PNG, JPG, JPEG, WEBP ou TXT.")

    try:
        dados = decodificar_base64_seguro(conteudo_base64)
    except ValueError:
        raise _entrada_invalida("documento_base64 não é um Base64 válido.") from None

    if formato in FORMATOS_PDF:
        _validar_pdf(dados)
    elif formato in FORMATOS_IMAGEM:
        _validar_imagem(dados)


def validar_entrada(entrada: DocumentoEntrada) -> None:
    """Levanta HTTPException 422 se a entrada não puder ser processada. Não grava nada.

    O tipo_arquivo só é conferido quando há documento_base64: um documento que chega só como
    texto (já passou por OCR, ou é digital) pode ter qualquer tipo, como nos cenários oficiais.
    """
    if not PADRAO_DOCUMENTO_ID.fullmatch(entrada.documento_id):
        raise _entrada_invalida(
            "documento_id inválido: use de 1 a 64 caracteres entre letras, números e hífen."
        )

    tem_texto = bool((entrada.documento_texto or "").strip())
    conteudo_base64 = entrada.documento_base64 or ""
    if not (tem_texto or conteudo_base64.strip()):
        raise _entrada_invalida("Informe documento_texto (não vazio) ou documento_base64.")

    # O leitor decodifica qualquer documento_base64 não vazio; por isso a checagem vale mesmo
    # quando também há texto.
    if conteudo_base64:
        _validar_arquivo(entrada.tipo_arquivo, conteudo_base64)
