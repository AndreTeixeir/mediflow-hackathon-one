"""
MediFlow - Auditoria humana pela API (Human-in-the-Loop)
Hackathon ONE G10 - Grupo 10: MediFlow

Mapeamento: api/auditoria.py
Três rotas: a fila de documentos retidos (GET /api/v1/auditoria/pendentes), a decisão de quem revisa
(POST /api/v1/auditoria/{documento_id}/decisao) e o histórico (GET /api/v1/documentos).

A decisão move o documento do bucket de auditoria humana para o de rotina, de urgência ou de
rejeitados e grava, no próprio JSON, o registro `auditoria_humana`: quem decidiu, quando, o quê, por
quê, e também o que o grafo tinha decidido, para a trilha mostrar o que a IA decidiu e o que a pessoa
mudou. Nenhuma mensagem de erro repete o conteúdo de um documento.
"""

import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterable, Iterator, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from agente.grafo import ORDEM_DE_PRIORIDADE, status_para_destino
from api.cache_listagens import LISTAGENS
from api.dependencias import obter_repositorio
from api.triagem import bucket_do_destino
from api.validacao import PADRAO_DOCUMENTO_ID
from armazenamento.repositorio import (
    FalhaArmazenamento,
    InfoObjeto,
    NomesBuckets,
    ObjetoIlegivel,
    ObjetoJaExiste,
    ObjetoNaoEncontrado,
    RepositorioDocumentos,
    obter_buckets,
)
from contratos.modelos import (
    ArmazenamentoOCI,
    DecisaoAuditoriaRequisicao,
    DecisaoRoteamento,
    DestinoAuditoria,
    DestinoRoteamento,
    DocumentoAuditado,
    ItemPendenteAuditoria,
    NivelPrioridade,
    RegistroAuditoriaHumana,
    ResumoDocumento,
    TriagemResposta,
)
from regras.regras_clinicas import (
    AVISO_CRM_INVALIDO,
    destino_por_tipo,
    gerar_notificacao_urgente,
    validar_crm,
)

logger = logging.getLogger("mediflow.api.auditoria")

router = APIRouter()

# A fila e o histórico devolvem no máximo isto. Cada item é uma leitura no armazenamento (na OCI, uma
# requisição da cota mensal): o limite e o cache de api/cache_listagens.py seguram o custo de cada consulta.
LIMITE_DE_ITENS = 50
# Quando a entrada original já não está em `recebidos` (por exemplo, apagada pela regra de ciclo de vida).
CANAL_NAO_INFORMADO = "canal de origem não informado"

# Duas decisões ao mesmo tempo sobre o mesmo documento, para destinos diferentes, gravariam o documento
# nos dois: a criação exclusiva só protege o MESMO destino. A trava vale para este processo (a API roda
# com um worker); com mais de um, a regra passa a ser do armazenamento.
_trava_das_decisoes = threading.Lock()


def _agora() -> datetime:
    return datetime.now(timezone.utc)


@contextmanager
def _falha_de_armazenamento_vira_503() -> Iterator[None]:
    try:
        yield
    except FalhaArmazenamento as erro:
        # A mensagem já é segura (sem credenciais nem OCIDs): vai para o log.
        logger.error("Falha de armazenamento na auditoria: %s", erro)
        raise HTTPException(status_code=503, detail="Falha ao acessar o armazenamento.") from None


def _triagem_valida(documento: dict, nome_objeto: str) -> Optional[TriagemResposta]:
    try:
        return TriagemResposta.model_validate(documento)
    except ValidationError:
        # Só o nome do objeto: a mensagem do Pydantic repetiria trechos do conteúdo.
        logger.warning("Objeto ignorado: %s não tem o formato de uma triagem.", nome_objeto)
        return None


def _ler_triagens(
    repositorio: RepositorioDocumentos, objetos: Iterable[Tuple[str, InfoObjeto]]
) -> Iterator[Tuple[str, InfoObjeto, TriagemResposta]]:
    """Lê cada objeto. O que sumiu entre a listagem e a leitura, ou não é uma triagem, é pulado: um objeto
    ruim não pode travar a fila inteira. Uma falha de verdade do armazenamento sobe (503)."""
    for bucket, info in objetos:
        try:
            documento = repositorio.ler_json(bucket, info.nome)
        except ObjetoNaoEncontrado:
            continue
        except ObjetoIlegivel:
            logger.warning("Objeto ignorado: %s tem conteúdo que não é um JSON de objeto.", info.nome)
            continue
        triagem = _triagem_valida(documento, info.nome)
        if triagem is not None:
            yield bucket, info, triagem


# ============================================================================
# GET /api/v1/auditoria/pendentes e GET /api/v1/documentos
# ============================================================================

def _calcular_pendentes(repositorio: RepositorioDocumentos) -> List[ItemPendenteAuditoria]:
    bucket = obter_buckets().auditoria_humana
    with _falha_de_armazenamento_vira_503():
        objetos = repositorio.listar(bucket)[:LIMITE_DE_ITENS]
        return [
            ItemPendenteAuditoria(
                documento_id=triagem.documento_id,
                tipo_documento=triagem.classificacao.tipo_documento,
                nivel_prioridade=triagem.classificacao.nivel_prioridade,
                score_confianca_classificacao=triagem.classificacao.score_confianca_classificacao,
                justificativa_roteamento=triagem.decisao_roteamento.justificativa_roteamento,
                dados_extraidos=triagem.dados_extraidos,
                data=info.criado_em,
            )
            for _, info, triagem in _ler_triagens(repositorio, [(bucket, o) for o in objetos])
        ]


def _calcular_historico(repositorio: RepositorioDocumentos) -> List[ResumoDocumento]:
    buckets = obter_buckets()
    with _falha_de_armazenamento_vira_503():
        todos = [
            (bucket, objeto)
            for bucket in (buckets.rotina, buckets.urgentes, buckets.auditoria_humana, buckets.rejeitados)
            for objeto in repositorio.listar(bucket)
        ]
        recentes = sorted(todos, key=lambda item: (item[1].criado_em, item[1].nome), reverse=True)[:LIMITE_DE_ITENS]
        return [
            ResumoDocumento(
                documento_id=triagem.documento_id,
                bucket=bucket,
                status=triagem.status,
                nivel_prioridade=triagem.classificacao.nivel_prioridade,
                destino_principal=triagem.decisao_roteamento.destino_principal,
                data=info.criado_em,
            )
            for bucket, info, triagem in _ler_triagens(repositorio, recentes)
        ]


# O 503 do armazenamento levanta dentro do cálculo, então nunca entra no cache.
@router.get("/api/v1/auditoria/pendentes", response_model=List[ItemPendenteAuditoria])
def listar_pendentes(repositorio: RepositorioDocumentos = Depends(obter_repositorio)):
    """A fila de revisão humana: do documento que espera há mais tempo para o mais novo (até 50).
    O resultado vale por um minuto, ou até uma triagem ou decisão registrada."""
    return LISTAGENS.obter("pendentes", lambda: _calcular_pendentes(repositorio))


@router.get("/api/v1/documentos", response_model=List[ResumoDocumento])
def listar_documentos(repositorio: RepositorioDocumentos = Depends(obter_repositorio)):
    """O histórico dos buckets de rotina, urgentes, auditoria humana e rejeitados, do mais novo (até 50).
    O resultado vale por um minuto, ou até uma triagem ou decisão registrada."""
    return LISTAGENS.obter("documentos", lambda: _calcular_historico(repositorio))


# ============================================================================
# POST /api/v1/auditoria/{documento_id}/decisao
# ============================================================================

def _nivel_minimo(atual: NivelPrioridade, minimo: NivelPrioridade) -> NivelPrioridade:
    """Eleva a prioridade até o mínimo, sem rebaixar uma que já seja maior."""
    return atual if ORDEM_DE_PRIORIDADE.index(atual) >= ORDEM_DE_PRIORIDADE.index(minimo) else minimo


def aplicar_decisao(
    triagem: TriagemResposta,
    decisao: DecisaoAuditoriaRequisicao,
    buckets: NomesBuckets,
    nome_objeto: str,
    canal_origem: str,
    decidido_em: datetime,
) -> DocumentoAuditado:
    """O documento como fica depois da decisão humana. Não grava nada."""
    classificacao = triagem.classificacao
    notificacao = None

    if decisao.destino == DestinoAuditoria.URGENTE:
        destino = DestinoRoteamento.FILA_EMERGENCIA
        resultado = "enviado para a urgência"
        classificacao = classificacao.model_copy(
            update={"nivel_prioridade": _nivel_minimo(classificacao.nivel_prioridade, NivelPrioridade.URGENTE)}
        )
        medico = triagem.dados_extraidos.medico_solicitante
        crm_valido, motivo_crm = validar_crm(medico.crm if medico else None)
        aviso = None if crm_valido else f"{AVISO_CRM_INVALIDO} ({motivo_crm})."
        notificacao = gerar_notificacao_urgente(triagem.dados_extraidos, canal_origem, aviso)
    elif decisao.destino == DestinoAuditoria.REJEITADO:
        destino = DestinoRoteamento.DOCUMENTO_REJEITADO
        resultado = "rejeitado"
    else:
        destino = destino_por_tipo(classificacao.tipo_documento)
        resultado = "aprovado como rotina"

    return DocumentoAuditado(
        status=status_para_destino(destino),
        documento_id=triagem.documento_id,
        classificacao=classificacao,
        dados_extraidos=triagem.dados_extraidos,
        decisao_roteamento=DecisaoRoteamento(
            destino_principal=destino,
            requer_auditoria_humana=False,
            justificativa_roteamento=(
                f"Documento {resultado} pela auditoria humana ({decisao.revisor}). Parecer: {decisao.justificativa}"
            ),
            notificacao_gerada=notificacao,
        ),
        # status_backup="sucesso" só vale porque o documento é gravado logo em seguida e a exceção sobe se falhar.
        armazenamento_oci=ArmazenamentoOCI(
            bucket=bucket_do_destino(destino, buckets), caminho_objeto=nome_objeto, status_backup="sucesso"
        ),
        auditoria_humana=RegistroAuditoriaHumana(
            revisor=decisao.revisor,
            decidido_em=decidido_em,
            destino=decisao.destino,
            justificativa=decisao.justificativa,
            destino_original=triagem.decisao_roteamento.destino_principal,
            justificativa_original=triagem.decisao_roteamento.justificativa_roteamento,
        ),
    )


def _canal_de_origem(repositorio: RepositorioDocumentos, buckets: NomesBuckets, nome_objeto: str) -> str:
    """O canal da entrada original, gravada em `recebidos`. Sem ela, o alerta diz que não foi informado."""
    try:
        entrada = repositorio.ler_json(buckets.recebidos, nome_objeto)
    except ObjetoNaoEncontrado:
        return CANAL_NAO_INFORMADO
    except ObjetoIlegivel:
        # Uma entrada ilegível não impede a decisão de quem revisa (o armazenamento que falha de fato sobe como 503).
        logger.warning("Entrada original ilegível em recebidos: %s. O alerta sai sem o canal de origem.", nome_objeto)
        return CANAL_NAO_INFORMADO
    canal = entrada.get("canal_origem")
    return canal if isinstance(canal, str) and canal.strip() else CANAL_NAO_INFORMADO


@router.post("/api/v1/auditoria/{documento_id}/decisao", response_model=DocumentoAuditado)
def decidir(
    documento_id: str,
    decisao: DecisaoAuditoriaRequisicao,
    repositorio: RepositorioDocumentos = Depends(obter_repositorio),
):
    """Move um documento retido para rotina, urgente ou rejeitados e grava o registro da auditoria."""
    if not PADRAO_DOCUMENTO_ID.fullmatch(documento_id):
        raise HTTPException(
            status_code=422,
            detail="documento_id inválido: use de 1 a 64 caracteres entre letras, números e hífen.",
        )

    buckets = obter_buckets()
    nome_objeto = f"{documento_id}.json"

    with _trava_das_decisoes, _falha_de_armazenamento_vira_503():
        try:
            documento = repositorio.ler_json(buckets.auditoria_humana, nome_objeto)
        except ObjetoNaoEncontrado:
            raise HTTPException(status_code=404, detail="Documento não encontrado entre os pendentes de auditoria.") from None
        except ObjetoIlegivel:
            logger.warning("Documento pendente ilegível: %s.", nome_objeto)
            raise HTTPException(status_code=503, detail="O documento pendente está ilegível no armazenamento.") from None

        triagem = _triagem_valida(documento, nome_objeto)
        if triagem is None:
            raise HTTPException(status_code=503, detail="O documento pendente está ilegível no armazenamento.")

        canal = (
            _canal_de_origem(repositorio, buckets, nome_objeto)
            if decisao.destino == DestinoAuditoria.URGENTE
            else CANAL_NAO_INFORMADO
        )
        decidido = aplicar_decisao(triagem, decisao, buckets, nome_objeto, canal, _agora())

        try:
            repositorio.mover_json(
                buckets.auditoria_humana,
                decidido.armazenamento_oci.bucket,
                nome_objeto,
                decidido.model_dump(mode="json"),
            )
        except ObjetoJaExiste:
            raise HTTPException(status_code=409, detail="Já existe um documento com este ID no destino da decisão.") from None
        except ObjetoNaoEncontrado:
            raise HTTPException(status_code=404, detail="Documento não encontrado entre os pendentes de auditoria.") from None

        # O documento mudou de bucket: a fila e o histórico em cache já não valem.
        LISTAGENS.limpar()

    return decidido
