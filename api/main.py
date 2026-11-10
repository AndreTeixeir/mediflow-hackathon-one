"""
MediFlow - API REST (FastAPI)
Hackathon ONE G10 - Grupo 10: MediFlow

Mapeamento: api/main.py
Execução (a partir da raiz): python -m uvicorn api.main:app --reload
"""

import logging
from functools import lru_cache

from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from api.triagem import processar_triagem
from api.validacao import validar_entrada
from armazenamento.repositorio import (
    FalhaArmazenamento,
    ObjetoJaExiste,
    RepositorioDocumentos,
    criar_repositorio,
)
from contratos.modelos import DocumentoEntrada, TriagemResposta

load_dotenv()

logger = logging.getLogger("mediflow.api")

app = FastAPI(title="MediFlow API", version="0.1.0")


@lru_cache(maxsize=1)
def obter_repositorio() -> RepositorioDocumentos:
    """Criado uma vez, no primeiro uso. Os testes o substituem com app.dependency_overrides."""
    return criar_repositorio()


@app.exception_handler(RequestValidationError)
async def tratar_corpo_invalido(request, exc):
    # O handler padrão do FastAPI devolve também o campo "input" (o valor recusado): com um corpo
    # binário (multipart, por exemplo) ele quebra com UnicodeDecodeError e a resposta vira 500, e
    # com um corpo de texto ele repetiria o documento clínico enviado. Aqui só saem loc, msg e type.
    erros = [
        {"loc": list(erro.get("loc", ())), "msg": erro.get("msg", ""), "type": erro.get("type", "")}
        for erro in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": erros})


@app.exception_handler(ObjetoJaExiste)
async def tratar_objeto_ja_existe(request, exc):
    return JSONResponse(
        status_code=409,
        content={"detail": "Documento com este documento_id já foi recebido."},
    )


@app.exception_handler(FalhaArmazenamento)
async def tratar_falha_armazenamento(request, exc):
    # A mensagem da exceção já é segura (sem credenciais nem OCIDs): vai para o log.
    logger.error("Falha de armazenamento: %s", exc)
    return JSONResponse(
        status_code=503,
        content={"detail": "Falha ao gravar o documento no armazenamento."},
    )


@app.get("/api/v1/saude")
def saude(repositorio: RepositorioDocumentos = Depends(obter_repositorio)):
    return {"status": "ok", "armazenamento": repositorio.nome}


@app.post("/api/v1/triagem", response_model=TriagemResposta)
def triagem(
    entrada: DocumentoEntrada,
    repositorio: RepositorioDocumentos = Depends(obter_repositorio),
):
    # Roda antes de qualquer gravação: entrada inválida vira 422 e nada é gravado.
    validar_entrada(entrada)

    return processar_triagem(entrada, repositorio)
