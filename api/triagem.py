"""
MediFlow - Fluxo de Triagem
Hackathon ONE G10 - Grupo 10: MediFlow

Mapeamento: api/triagem.py
Recebe o documento, grava a entrada, classifica, aplica as regras clínicas e grava a
resposta no bucket do destino. As exceções do repositório (ObjetoJaExiste e
FalhaArmazenamento) sobem para a camada HTTP, que as converte em 409 e 503.
"""

import logging

from agente import executar_triagem_mediflow
from armazenamento.repositorio import (
    NomesBuckets,
    ObjetoJaExiste,
    RepositorioDocumentos,
    obter_buckets,
)
from contratos.modelos import (
    ArmazenamentoOCI,
    DestinoRoteamento,
    DocumentoEntrada,
    TriagemResposta,
)

logger = logging.getLogger("mediflow.api.triagem")


def _bucket_destino(destino: DestinoRoteamento, buckets: NomesBuckets) -> str:
    por_destino = {
        DestinoRoteamento.FILA_EMERGENCIA: buckets.urgentes,
        DestinoRoteamento.FILA_REVISAO_HUMANA: buckets.auditoria_humana,
        DestinoRoteamento.FARMACIA_HOSPITALAR: buckets.rotina,
        DestinoRoteamento.AUDITORIA_AUTORIZACOES: buckets.rotina,
        DestinoRoteamento.PRONTUARIO_ELETRONICO: buckets.rotina,
        DestinoRoteamento.DOCUMENTO_REJEITADO: buckets.rejeitados,
    }
    return por_destino[destino]


def _montar_resposta(entrada: DocumentoEntrada, buckets: NomesBuckets, nome_objeto: str) -> TriagemResposta:
    # Sempre executa o Grafo de Decisão completo do LangGraph (executar_triagem_mediflow): ele
    # classifica, extrai, aplica as regras clínicas e devolve a decisão e o status.
    resposta_grafo = executar_triagem_mediflow(entrada)
    classificacao = resposta_grafo.classificacao
    dados = resposta_grafo.dados_extraidos
    decisao = resposta_grafo.decisao_roteamento
    status = resposta_grafo.status

    bucket_destino = _bucket_destino(decisao.destino_principal, buckets)

    # status_backup="sucesso" só vale porque processar_triagem grava a resposta logo em
    # seguida e deixa a exceção subir se falhar: a resposta nunca sai afirmando um backup
    # que não houve.
    return TriagemResposta(
        status=status,
        documento_id=entrada.documento_id,
        classificacao=classificacao,
        dados_extraidos=dados,
        decisao_roteamento=decisao,
        armazenamento_oci=ArmazenamentoOCI(
            bucket=bucket_destino,
            caminho_objeto=nome_objeto,
            status_backup="sucesso",
        ),
    )


def _compensar(repositorio: RepositorioDocumentos, bucket: str, nome_objeto: str, documento_id: str) -> None:
    """Desfaz a entrada em `recebidos`. Nunca levanta: quem chama relança a exceção original."""
    try:
        repositorio.apagar(bucket, nome_objeto)
    except Exception as erro:
        # Só o documento_id, o bucket e o tipo do erro: nada da mensagem original do SDK.
        logger.error(
            "Falha ao desfazer a entrada: documento_id=%s bucket=%s erro=%s. "
            "O documento pode ficar preso em recebidos e exigir limpeza manual.",
            documento_id,
            bucket,
            type(erro).__name__,
        )


def processar_triagem(entrada: DocumentoEntrada, repositorio: RepositorioDocumentos) -> TriagemResposta:
    """Grava a entrada, classifica, roteia e grava a resposta no bucket do destino.

    A entrada é gravada em `recebidos` primeiro. Se o `documento_id` já existir lá,
    `ObjetoJaExiste` sobe (409) sem compensação: aquela entrada é de outra requisição.

    Depois disso, se QUALQUER passo falhar (análise, regras, montagem ou gravação no
    destino), a entrada é apagada de `recebidos` (compensação) e a exceção original é
    relançada. Assim uma falha temporária não trava o `documento_id`: o reenvio funciona.
    FalhaArmazenamento vira 503; qualquer outra exceção (bug) vira 500 genérico.

    Casos raros que podem exigir limpeza manual em `recebidos`:
    - a própria compensação falha (erro registrado em log com documento_id e bucket; a
      exceção relançada continua sendo a original);
    - o destino já continha o objeto (`ObjetoJaExiste` na segunda gravação): é um estado
      anterior inconsistente. Nada é apagado, o erro sobe como 409 e um aviso é registrado.
    """
    buckets = obter_buckets()
    nome_objeto = f"{entrada.documento_id}.json"

    repositorio.salvar_json(buckets.recebidos, nome_objeto, entrada.model_dump(mode="json"))

    try:
        resposta = _montar_resposta(entrada, buckets, nome_objeto)
        repositorio.salvar_json(
            resposta.armazenamento_oci.bucket, nome_objeto, resposta.model_dump(mode="json")
        )
    except ObjetoJaExiste:
        logger.warning(
            "O destino já continha o objeto: documento_id=%s. A entrada em %s foi preservada "
            "(estado anterior inconsistente; pode exigir limpeza manual).",
            entrada.documento_id,
            buckets.recebidos,
        )
        raise
    except Exception:
        _compensar(repositorio, buckets.recebidos, nome_objeto, entrada.documento_id)
        raise

    return resposta
