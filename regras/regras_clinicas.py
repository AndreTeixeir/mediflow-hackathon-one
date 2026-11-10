"""
MediFlow - Motor de Validação Clínica, Consistência e Score de Confiança
Hackathon ONE G10 - Grupo 10: MediFlow

Responsável: Lethicia
Mapeamento: regras/regras_clinicas.py
Importação compatível com o padrão do repositório: from contratos.modelos import ...
"""

import re
from typing import Tuple, List, Optional
from contratos.modelos import (
    DadosExtraidos,
    ClassificacaoDocumento,
    DecisaoRoteamento,
    DestinoRoteamento,
    NivelPrioridade,
    NotificacaoGerada,
    TipoDocumento
)

# Palavras-chave de alto risco / emergência médica
TERMOS_URGENCIA_CRITICA = [
    "tep", "tromboembolismo pulmonar", "embolia pulmonar",
    "infarto", "iam", "isquemia aguda", "avc", "acidente vascular cerebral",
    "hemorragia", "parada cardiorrespiratoria", "pcr",
    "choque septico", "sepse", "anafilaxia", "dispneia subita",
    "insuficiencia respiratoria aguda", "pneumotorax hipertensivo"
]

# As 27 unidades da federação: a UF de um CRM só vale se for uma destas.
UFS_VALIDAS = frozenset({
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA",
    "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
})

TERMOS_PRIORITARIOS = [
    "urgente", "emergencia", "prioritario", "critico", "gravidade",
    "correlacao clinica urgente", "encaminhamento imediato"
]


def validar_crm(crm: Optional[str]) -> Tuple[bool, str]:
    """
    Valida se o CRM possui formato razoável (números + UF, ex: 145892-SP).
    """
    if not crm or not crm.strip():
        return False, "CRM não informado ou vazio"
    
    crm_limpo = crm.strip().upper()
    padrao = r'(\d{3,8})'
    if re.search(padrao, crm_limpo):
        return True, "CRM Válido"
    
    return False, f"Formato de CRM suspeito ou inválido: '{crm}'"


def detectar_urgencia_clinica(texto: str, dados: DadosExtraidos) -> Tuple[bool, str]:
    """
    Analisa o texto do laudo/diagnóstico para identificar termos de urgência crítica.
    Retorna (is_urgente, justificativa).
    """
    corpus = f"{texto} {dados.diagnostico_principal or ''} {dados.observacoes_clinicas or ''}".lower()

    for termo in TERMOS_URGENCIA_CRITICA:
        if termo in corpus:
            return True, f"Achado crítico de alta gravidade ({termo.upper()}) detectado em paciente sintomático."

    for termo in TERMOS_PRIORITARIOS:
        if termo in corpus:
            return True, f"Indicativo de prioridade clínica urgente encontrado no documento ('{termo}')."

    return False, "Nenhum achado crítico de emergência imediata detectado."


def calcular_score_confianca(
    classificacao: ClassificacaoDocumento,
    dados: DadosExtraidos,
    texto_original: Optional[str] = None
) -> Tuple[float, List[str]]:
    """
    Calcula um score de consistência clínica (0.0 a 1.0) baseado em:
      - Score da LLM na classificação
      - Presença de paciente (nome, idade)
      - Presença e validade de médico/CRM
      - Presença de dados do exame ou medicamentos
      - Indicadores de ambiguidade ou texto ilegível
    """
    motivos_penalidade = []
    score_base = classificacao.score_confianca_classificacao

    # Penalidade por falta de nome do paciente
    if not dados.paciente or not dados.paciente.nome or len(dados.paciente.nome.strip()) < 3:
        score_base -= 0.20
        motivos_penalidade.append("Nome do paciente ausente ou incompleto")

    # Penalidade por CRM inválido ou ausente
    crm = dados.medico_solicitante.crm if dados.medico_solicitante else None
    crm_valido, crm_msg = validar_crm(crm)
    if not crm_valido:
        score_base -= 0.15
        motivos_penalidade.append(crm_msg)

    # Verificação de acordo com o tipo de documento
    tipo = classificacao.tipo_documento
    if tipo == TipoDocumento.RECEITUARIO.value:
        if not dados.medicamentos or len(dados.medicamentos) == 0:
            score_base -= 0.25
            motivos_penalidade.append("Receituário sem medicamentos identificados")
    elif tipo in [TipoDocumento.LAUDO_EXAME_IMAGEM.value, TipoDocumento.LAUDO_EXAME_LABORATORIAL.value]:
        if not dados.exame_realizado and not dados.diagnostico_principal:
            score_base -= 0.25
            motivos_penalidade.append("Laudo sem identificação de exame realizado ou diagnóstico conclusivo")

    # Verificação de texto original ambíguo ou ilegível
    if texto_original:
        texto_lower = texto_original.lower()
        if any(term in texto_lower for term in ["[ilegível]", "rasurado", "incompreensível", "foto borrada", "não identificado"]):
            score_base -= 0.35
            motivos_penalidade.append("Documento contém trechos ilegíveis ou rasuras detectadas")

    score_final = max(0.0, min(1.0, round(score_base, 2)))
    return score_final, motivos_penalidade


def executar_roteamento_clinico(
    classificacao: ClassificacaoDocumento,
    dados: DadosExtraidos,
    texto_original: str = "",
    canal_origem: str = "Pronto_Socorro",
    limiar_auditoria_humana: float = 0.75
) -> DecisaoRoteamento:
    """
    Aplica as regras condicionais de negócio para definir o destino do documento,
    avaliando urgência clínica e necessidade de Human-in-the-Loop.
    """
    is_urgente, justificativa_urgencia = detectar_urgencia_clinica(texto_original, dados)
    score_confianca, pendencias = calcular_score_confianca(classificacao, dados, texto_original)

    # Regra 1: Caso Urgente Crítico (Prioridade máxima de atendimento)
    if is_urgente:
        paciente_nome = dados.paciente.nome if dados.paciente and dados.paciente.nome else "Paciente não identificado"
        diag = dados.diagnostico_principal or "Quadro agudo"
        
        notificacao = NotificacaoGerada(
            canal="Alerta_Plantao_Medico",
            mensagem=f"ALERTA URGENTE: Laudo critico de {diag} para paciente {paciente_nome} no {canal_origem}."
        )
        return DecisaoRoteamento(
            destino_principal=DestinoRoteamento.FILA_EMERGENCIA,
            requer_auditoria_humana=False,
            justificativa_roteamento=justificativa_urgencia,
            notificacao_gerada=notificacao
        )

    # Regra 2: Caso Ambíguo ou Baixa Confiança -> Human-in-the-Loop (Auditoria)
    if score_confianca < limiar_auditoria_humana or len(pendencias) >= 2:
        justificativa = f"Score de confiança baixo ({score_confianca:.2f}). Inconsistências: {'; '.join(pendencias)}."
        return DecisaoRoteamento(
            destino_principal=DestinoRoteamento.FILA_REVISAO_HUMANA,
            requer_auditoria_humana=True,
            justificativa_roteamento=justificativa,
            notificacao_gerada=None
        )

    # Regra 3: Roteamento por tipo de documento clínico
    tipo = classificacao.tipo_documento
    
    if tipo == TipoDocumento.RECEITUARIO.value:
        return DecisaoRoteamento(
            destino_principal=DestinoRoteamento.FARMACIA_HOSPITALAR,
            requer_auditoria_humana=False,
            justificativa_roteamento="Receituário clínico validado com sucesso, encaminhado para separação farmacêutica.",
            notificacao_gerada=None
        )
    elif tipo == TipoDocumento.GUIA_SOLICITACAO.value:
        return DecisaoRoteamento(
            destino_principal=DestinoRoteamento.AUDITORIA_AUTORIZACOES,
            requer_auditoria_humana=False,
            justificativa_roteamento="Guia de procedimento clínico encaminhada para auditoria de convênio e autorizações TISS/TUSS.",
            notificacao_gerada=None
        )
    else:
        # Laudos de rotina, atestados, relatórios de alta aprovados
        return DecisaoRoteamento(
            destino_principal=DestinoRoteamento.PRONTUARIO_ELETRONICO,
            requer_auditoria_humana=False,
            justificativa_roteamento="Documento de rotina sem achados críticos arquivado no Prontuário Eletrônico do Paciente.",
            notificacao_gerada=None
        )
