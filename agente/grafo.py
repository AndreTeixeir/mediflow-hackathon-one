"""
MediFlow - Grafo de Decisão Condicional do Agente Autônomo (LangGraph)
Hackathon ONE G10 - Grupo 10: MediFlow

Responsável: Ingrid Soares
Mapeamento: agente/grafo.py
Fluxo: Ingestão Multimodal -> Classificação & Extração (LLM) -> Validação & Score de Confiança
       -> Aresta Condicional (Emergência | Revisão Humana HITL | Fluxo Padrão) -> Resposta Final.
Fronteira de arquitetura: O grafo decide (classifica, extrai, valida e roteia) e retorna
`armazenamento_oci=None`; a camada da API grava de fato e preenche `armazenamento_oci`.
"""

from typing import List, Literal, Tuple, TypedDict

from langgraph.graph import END, START, StateGraph

from agente.leitor_documentos import ConteudoDocumentoIngerido, ingerir_documento_clinico
from agente.visao_multimodal import analisar_documento_com_gemini
from contratos.modelos import (
    ClassificacaoDocumento,
    DadosExtraidos,
    DecisaoRoteamento,
    DestinoRoteamento,
    DocumentoEntrada,
    NivelPrioridade,
    StatusProcessamento,
    TriagemResposta,
)
from regras.regras_clinicas import (
    NivelUrgencia,
    calcular_score_confianca,
    classificar_nivel_urgencia,
    executar_roteamento_clinico,
)


class EstadoAgenteMediFlow(TypedDict, total=False):
    """
    Estado tipado que percorre os nós do Grafo de Decisão no LangGraph.
    """
    entrada: DocumentoEntrada
    limiar_auditoria_humana: float
    conteudo_ingerido: ConteudoDocumentoIngerido
    texto_processado: str
    classificacao: ClassificacaoDocumento
    dados_extraidos: DadosExtraidos
    score_confianca: float
    pendencias_consistencia: List[str]
    caminho_decisao: Literal["emergencia", "revisao_humana", "fluxo_padrao"]
    decisao_roteamento: DecisaoRoteamento
    status_final: StatusProcessamento
    resposta_final: TriagemResposta


# Da menor para a maior: um termo prioritário eleva a prioridade até Prioritário, mas nunca rebaixa
# uma prioridade maior que o LLM já tenha dado.
ORDEM_DE_PRIORIDADE = (
    NivelPrioridade.ROTINA,
    NivelPrioridade.PRIORITARIO,
    NivelPrioridade.URGENTE,
    NivelPrioridade.EMERGENCIA,
)

# Caminho do grafo para cada destino das regras; os demais destinos seguem o fluxo padrão.
CAMINHO_POR_DESTINO = {
    DestinoRoteamento.FILA_EMERGENCIA: "emergencia",
    DestinoRoteamento.FILA_REVISAO_HUMANA: "revisao_humana",
}


def status_para_destino(destino: DestinoRoteamento) -> StatusProcessamento:
    """Fila_Revisao_Humana implica sempre pendente_auditoria, Documento_Rejeitado implica rejeitado
    (só a auditoria humana o produz) e qualquer outro destino, processado."""
    if destino == DestinoRoteamento.FILA_REVISAO_HUMANA:
        return StatusProcessamento.PENDENTE_AUDITORIA
    if destino == DestinoRoteamento.DOCUMENTO_REJEITADO:
        return StatusProcessamento.REJEITADO
    return StatusProcessamento.PROCESSADO


# ============================================================================
# NÓS DO GRAFO DE DECISÃO (LANGGRAPH NODES)
# ============================================================================

def no_ingerir_documento(estado: EstadoAgenteMediFlow) -> EstadoAgenteMediFlow:
    """
    Nó 1: Recebe o DocumentoEntrada e executa a leitura (Texto, PDF digital,
    PDF escaneado ou Imagem em Base64).
    """
    entrada = estado["entrada"]
    conteudo = ingerir_documento_clinico(entrada)
    return {
        "conteudo_ingerido": conteudo,
        "texto_processado": conteudo.texto_extraido,
    }


def no_classificar_e_extrair(estado: EstadoAgenteMediFlow) -> EstadoAgenteMediFlow:
    """
    Nó 2: Aciona o pipeline de Visão Multimodal / LLM (Google Gemini) ou fallback
    estruturado para classificar o documento e extrair entidades clínicas.
    """
    entrada = estado["entrada"]
    conteudo = estado["conteudo_ingerido"]
    canal = entrada.canal_origem or "Pronto_Socorro"

    classificacao, dados_extraidos, texto_efetivo = analisar_documento_com_gemini(
        conteudo=conteudo,
        canal_origem=canal,
    )
    return {
        "classificacao": classificacao,
        "dados_extraidos": dados_extraidos,
        "texto_processado": texto_efetivo,
    }


def no_avaliar_consistencia_e_urgencia(estado: EstadoAgenteMediFlow) -> EstadoAgenteMediFlow:
    """
    Nó 3: Avalia consistência clínica, calcula o Score de Confiança consolidado e pede às regras
    a decisão de roteamento (urgência crítica, revisão humana ou fluxo padrão).

    A classificação que chega aqui é a do LLM, com o score original. A penalidade de consistência
    é aplicada UMA vez: o score consolidado e a decisão partem da mesma classificação. Os nós de
    roteamento seguintes não recalculam nada, só aplicam o que esta decisão já definiu.
    """
    conteudo = estado.get("conteudo_ingerido")
    if conteudo is not None and conteudo.falha_leitura:
        # O arquivo não abriu: não há o que avaliar. Revisão humana, com o motivo na justificativa.
        decisao = DecisaoRoteamento(
            destino_principal=DestinoRoteamento.FILA_REVISAO_HUMANA,
            requer_auditoria_humana=True,
            justificativa_roteamento=f"{conteudo.falha_leitura} O documento não foi analisado e segue para a revisão humana.",
            notificacao_gerada=None,
        )
        return {
            "score_confianca": 0.0,
            "pendencias_consistencia": [conteudo.falha_leitura],
            "decisao_roteamento": decisao,
            "caminho_decisao": "revisao_humana",
        }

    entrada = estado["entrada"]
    classificacao = estado["classificacao"]
    dados = estado["dados_extraidos"]
    texto = estado.get("texto_processado", "")
    canal = entrada.canal_origem or "Pronto_Socorro"
    limiar = estado.get("limiar_auditoria_humana", 0.75)

    score_calculado, pendencias = calcular_score_confianca(classificacao, dados, texto)
    decisao = executar_roteamento_clinico(
        classificacao=classificacao,
        dados=dados,
        texto_original=texto,
        canal_origem=canal,
        limiar_auditoria_humana=limiar,
    )

    # O score da resposta é o consolidado, o mesmo que a justificativa do roteamento cita. O nível
    # de prioridade vem da função própria das regras, nunca da leitura do texto da justificativa.
    nivel_prioridade = classificacao.nivel_prioridade
    if (
        classificar_nivel_urgencia(texto, dados).nivel == NivelUrgencia.PRIORITARIO
        and ORDEM_DE_PRIORIDADE.index(nivel_prioridade) < ORDEM_DE_PRIORIDADE.index(NivelPrioridade.PRIORITARIO)
    ):
        nivel_prioridade = NivelPrioridade.PRIORITARIO
    classificacao = classificacao.model_copy(
        update={"score_confianca_classificacao": score_calculado, "nivel_prioridade": nivel_prioridade}
    )

    return {
        "classificacao": classificacao,
        "score_confianca": score_calculado,
        "pendencias_consistencia": pendencias,
        "decisao_roteamento": decisao,
        "caminho_decisao": CAMINHO_POR_DESTINO.get(decisao.destino_principal, "fluxo_padrao"),
    }


def decidir_caminho_triagem(
    estado: EstadoAgenteMediFlow,
) -> Literal["no_rotear_emergencia", "no_rotear_revisao_humana", "no_rotear_fluxo_padrao"]:
    """
    Função de Aresta Condicional (Conditional Edge) do LangGraph:
    Direciona o fluxo para Emergência, Revisão Humana (HITL) ou Roteamento Padrão.
    """
    caminho = estado.get("caminho_decisao", "fluxo_padrao")
    if caminho == "emergencia":
        return "no_rotear_emergencia"
    if caminho == "revisao_humana":
        return "no_rotear_revisao_humana"
    return "no_rotear_fluxo_padrao"


def no_rotear_emergencia(estado: EstadoAgenteMediFlow) -> EstadoAgenteMediFlow:
    """
    Nó 4A (Ramo Condicional de Urgência Crítica):
    Encaminha para Fila de Emergência Médica (a notificação imediata ao plantão já vem na decisão).
    """
    classificacao = estado["classificacao"].model_copy(
        update={"nivel_prioridade": NivelPrioridade.URGENTE}
    )
    return {
        "classificacao": classificacao,
        "status_final": status_para_destino(estado["decisao_roteamento"].destino_principal),
    }


def no_rotear_revisao_humana(estado: EstadoAgenteMediFlow) -> EstadoAgenteMediFlow:
    """
    Nó 4B (Ramo Condicional Human-in-the-Loop):
    Retém documentos com baixa confiança, dados ausentes ou rasuras para a
    Fila de Revisão Humana (`status = pendente_auditoria`).
    """
    return {"status_final": status_para_destino(estado["decisao_roteamento"].destino_principal)}


def no_rotear_fluxo_padrao(estado: EstadoAgenteMediFlow) -> EstadoAgenteMediFlow:
    """
    Nó 4C (Ramo Condicional de Rotina Aprovada):
    Encaminha automaticamente para Farmácia Hospitalar, Auditoria de Autorizações
    ou Prontuário Eletrônico do Paciente.
    """
    return {"status_final": status_para_destino(estado["decisao_roteamento"].destino_principal)}


def no_consolidar_resposta(estado: EstadoAgenteMediFlow) -> EstadoAgenteMediFlow:
    """
    Nó 5: Consolida e valida o contrato de saída `TriagemResposta` (Pydantic).
    `armazenamento_oci` permanece `None` para que a API preencha somente após a gravação real.
    """
    entrada = estado["entrada"]
    resposta = TriagemResposta(
        status=estado["status_final"],
        documento_id=entrada.documento_id,
        classificacao=estado["classificacao"],
        dados_extraidos=estado["dados_extraidos"],
        decisao_roteamento=estado["decisao_roteamento"],
        armazenamento_oci=None,
    )
    return {"resposta_final": resposta}


# ============================================================================
# CONSTRUÇÃO E EXECUÇÃO DO GRAFO (STATEGRAPH)
# ============================================================================

def construir_grafo_mediflow():
    """
    Monta e compila o StateGraph do LangGraph com todos os nós e arestas condicionais.
    """
    construtor = StateGraph(EstadoAgenteMediFlow)

    construtor.add_node("no_ingerir_documento", no_ingerir_documento)
    construtor.add_node("no_classificar_e_extrair", no_classificar_e_extrair)
    construtor.add_node("no_avaliar_consistencia_e_urgencia", no_avaliar_consistencia_e_urgencia)
    construtor.add_node("no_rotear_emergencia", no_rotear_emergencia)
    construtor.add_node("no_rotear_revisao_humana", no_rotear_revisao_humana)
    construtor.add_node("no_rotear_fluxo_padrao", no_rotear_fluxo_padrao)
    construtor.add_node("no_consolidar_resposta", no_consolidar_resposta)

    construtor.add_edge(START, "no_ingerir_documento")
    construtor.add_edge("no_ingerir_documento", "no_classificar_e_extrair")
    construtor.add_edge("no_classificar_e_extrair", "no_avaliar_consistencia_e_urgencia")

    construtor.add_conditional_edges(
        "no_avaliar_consistencia_e_urgencia",
        decidir_caminho_triagem,
        {
            "no_rotear_emergencia": "no_rotear_emergencia",
            "no_rotear_revisao_humana": "no_rotear_revisao_humana",
            "no_rotear_fluxo_padrao": "no_rotear_fluxo_padrao",
        },
    )

    construtor.add_edge("no_rotear_emergencia", "no_consolidar_resposta")
    construtor.add_edge("no_rotear_revisao_humana", "no_consolidar_resposta")
    construtor.add_edge("no_rotear_fluxo_padrao", "no_consolidar_resposta")
    construtor.add_edge("no_consolidar_resposta", END)

    return construtor.compile()


GRAFO_MEDIFLOW = construir_grafo_mediflow()


def executar_triagem_mediflow(
    entrada: DocumentoEntrada,
    limiar_auditoria_humana: float = 0.75,
) -> TriagemResposta:
    """
    Função principal para consumo pela API FastAPI (André):
    Executa o Grafo de Decisão completo e retorna `TriagemResposta` com `armazenamento_oci=None`,
    cabendo à API persistir o resultado no bucket do destino e preencher `armazenamento_oci`.
    """
    estado_inicial: EstadoAgenteMediFlow = {
        "entrada": entrada,
        "limiar_auditoria_humana": limiar_auditoria_humana,
    }
    estado_final = GRAFO_MEDIFLOW.invoke(estado_inicial)
    return estado_final["resposta_final"]


def analisar_documento(
    entrada: DocumentoEntrada,
) -> Tuple[ClassificacaoDocumento, DadosExtraidos]:
    """
    Versão enxuta do pipeline, sem as regras de roteamento: recebe `DocumentoEntrada`, executa
    ingestão multimodal + extração e devolve `(ClassificacaoDocumento, DadosExtraidos)`.
    A API não a usa: ela chama `executar_triagem_mediflow`, que roda o grafo inteiro.
    """
    conteudo = ingerir_documento_clinico(entrada)
    classificacao, dados, _ = analisar_documento_com_gemini(
        conteudo=conteudo,
        canal_origem=entrada.canal_origem or "Pronto_Socorro",
    )
    return classificacao, dados


# Alias amigável para integração com a API REST
processar_documento = executar_triagem_mediflow
