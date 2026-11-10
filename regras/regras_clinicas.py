"""
MediFlow - Motor de Validação Clínica, Consistência e Score de Confiança
Hackathon ONE G10 - Grupo 10: MediFlow

Responsável: Lethicia
Mapeamento: regras/regras_clinicas.py
Importação compatível com o padrão do repositório: from contratos.modelos import ...
"""

import re
import unicodedata
from enum import Enum
from typing import Tuple, List, NamedTuple, Optional
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

# Negação: o termo não conta quando aparece até 5 palavras depois de um destes (o primeiro
# vocábulo do termo é uma das 5 palavras seguintes). Só vale dentro da mesma frase.
# Limitação conhecida: a negação DEPOIS do termo ("TEP descartado") não é tratada.
NEGADORES = (
    "sem", "ausencia de", "nao ha", "nega", "negativo para",
    "afastado", "afastada", "descartado", "descartada",
)
JANELA_DE_NEGACAO_EM_PALAVRAS = 5

# Começo do motivo (justificativa e alerta) quando o CRM do médico falta ou não é válido.
AVISO_CRM_INVALIDO = "CRM do médico ausente ou inválido"

# "PCR" é ambíguo: parada cardiorrespiratória ou proteína C reativa. Só conta como parada se a
# MESMA frase tiver uma palavra do primeiro grupo, e nunca conta se tiver uma do segundo.
CONTEXTO_DE_PARADA_DO_PCR = ("parada", "rcp", "reanimacao", "revertida", "ciclos")
CONTEXTO_DE_PROTEINA_DO_PCR = ("mg/l", "proteina c", "reativa")


# ============================================================================
# URGÊNCIA CLÍNICA: NORMALIZAÇÃO, PALAVRA INTEIRA, NEGAÇÃO
# ============================================================================

class NivelUrgencia(str, Enum):
    CRITICO = "critico"
    PRIORITARIO = "prioritario"
    NENHUM = "nenhum"


class AchadoUrgencia(NamedTuple):
    """O nível de urgência encontrado e o termo que o provocou (None quando o nível é nenhum)."""
    nivel: NivelUrgencia
    termo: Optional[str]


def normalizar_texto(texto: Optional[str]) -> str:
    """Minúsculas, sem acento e com os espaços colapsados. Texto e termos passam pelo mesmo caminho."""
    decomposto = unicodedata.normalize("NFKD", texto or "")
    sem_acento = "".join(c for c in decomposto if not unicodedata.combining(c))
    return " ".join(sem_acento.lower().split())


def _padrao_de_palavra_inteira(termo: str) -> re.Pattern:
    """O termo (palavra ou expressão) só casa inteiro: "iam" não casa dentro de "diariamente"."""
    return re.compile(r"\b" + re.escape(normalizar_texto(termo)) + r"\b")


_TERMOS_CRITICOS = [(termo, _padrao_de_palavra_inteira(termo)) for termo in TERMOS_URGENCIA_CRITICA]
_TERMOS_PRIORITARIOS = [(termo, _padrao_de_palavra_inteira(termo)) for termo in TERMOS_PRIORITARIOS]
_NEGADORES = [_padrao_de_palavra_inteira(negador) for negador in NEGADORES]
_CONTEXTO_PARADA = [_padrao_de_palavra_inteira(palavra) for palavra in CONTEXTO_DE_PARADA_DO_PCR]
_CONTEXTO_PROTEINA = [_padrao_de_palavra_inteira(palavra) for palavra in CONTEXTO_DE_PROTEINA_DO_PCR]

# Fim de frase: ponto, exclamação, interrogação ou ponto e vírgula seguidos de espaço, ou quebra de linha.
# O ponto dentro de um número ("3.2 mg/L") não separa frases.
_FRONTEIRA_DE_FRASE = re.compile(r"(?<=[.!?;])\s+|[\r\n]+")


def _frases_normalizadas(texto: Optional[str]) -> List[str]:
    return [normalizar_texto(frase) for frase in _FRONTEIRA_DE_FRASE.split(texto or "") if frase and frase.strip()]


def _termo_negado(frase: str, inicio_do_termo: int) -> bool:
    antes = frase[:inicio_do_termo]
    for negador in _NEGADORES:
        for achado in negador.finditer(antes):
            palavras_entre = len(antes[achado.end():].split())
            if palavras_entre < JANELA_DE_NEGACAO_EM_PALAVRAS:
                return True
    return False


def _pcr_e_parada(frase: str) -> bool:
    if any(padrao.search(frase) for padrao in _CONTEXTO_PROTEINA):
        return False
    return any(padrao.search(frase) for padrao in _CONTEXTO_PARADA)


def _termo_vale_na_frase(termo: str, padrao: re.Pattern, frase: str) -> bool:
    for achado in padrao.finditer(frase):
        if _termo_negado(frase, achado.start()):
            continue
        if termo == "pcr" and not _pcr_e_parada(frase):
            continue
        return True
    return False


def _primeiro_termo(frases: List[str], termos) -> Optional[str]:
    for termo, padrao in termos:
        if any(_termo_vale_na_frase(termo, padrao, frase) for frase in frases):
            return termo
    return None


def classificar_nivel_urgencia(texto: Optional[str], dados: DadosExtraidos) -> AchadoUrgencia:
    """
    Nível de urgência do documento: crítico, prioritário ou nenhum, e o termo que o provocou.

    É a única fonte do nível: quem precisa dele chama esta função, e nunca lê a justificativa.
    Texto, diagnóstico e observações são analisados em separado, frase a frase, para uma negação
    ou uma palavra de contexto de um campo não alcançar o outro.
    """
    frases: List[str] = []
    for segmento in (texto, dados.diagnostico_principal, dados.observacoes_clinicas):
        frases.extend(_frases_normalizadas(segmento))

    termo = _primeiro_termo(frases, _TERMOS_CRITICOS)
    if termo:
        return AchadoUrgencia(NivelUrgencia.CRITICO, termo)

    termo = _primeiro_termo(frases, _TERMOS_PRIORITARIOS)
    if termo:
        return AchadoUrgencia(NivelUrgencia.PRIORITARIO, termo)

    return AchadoUrgencia(NivelUrgencia.NENHUM, None)


def _justificativa_critica(termo: str) -> str:
    return f"Achado crítico de alta gravidade ({termo.upper()}) detectado em paciente sintomático."


def _justificativa_prioritaria(termo: str) -> str:
    return f"Termo de prioridade clínica encontrado no documento ('{termo}')."


# Os quatro formatos aceitos: 145892-SP, 145892/SP, CRM-SP 145892 e CRM/SP 145892.
# O número tem de 4 a 6 dígitos e a UF precisa ser uma das 27.
_FORMATOS_DE_CRM = (
    re.compile(r"(?P<numero>\d{4,6})[-/](?P<uf>[A-Z]{2})"),
    re.compile(r"CRM[-/](?P<uf>[A-Z]{2}) (?P<numero>\d{4,6})"),
)


def validar_crm(crm: Optional[str]) -> Tuple[bool, str]:
    """
    Valida o CRM: número de 4 a 6 dígitos com UF válida, num dos formatos 145892-SP, 145892/SP,
    CRM-SP 145892 ou CRM/SP 145892. Um CRM só com o número (sem UF) é inválido.
    """
    if not crm or not crm.strip():
        return False, "CRM não informado ou vazio"

    crm_limpo = " ".join(crm.strip().upper().split())
    for formato in _FORMATOS_DE_CRM:
        encontrado = formato.fullmatch(crm_limpo)
        if encontrado and encontrado.group("uf") in UFS_VALIDAS:
            return True, "CRM Válido"

    return False, f"Formato de CRM suspeito ou inválido: '{crm[:40]}'"


def detectar_urgencia_clinica(texto: str, dados: DadosExtraidos) -> Tuple[bool, str]:
    """
    Analisa o texto do laudo/diagnóstico para identificar termos de urgência crítica.
    Retorna (is_urgente, justificativa). `is_urgente` é verdadeiro só para o nível crítico; um
    termo prioritário gera revisão humana (ver executar_roteamento_clinico), não emergência.
    """
    achado = classificar_nivel_urgencia(texto, dados)
    if achado.nivel == NivelUrgencia.CRITICO:
        return True, _justificativa_critica(achado.termo)
    if achado.nivel == NivelUrgencia.PRIORITARIO:
        return False, _justificativa_prioritaria(achado.termo)
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


_JUSTIFICATIVA_POR_DESTINO = {
    DestinoRoteamento.FARMACIA_HOSPITALAR: "Receituário clínico validado com sucesso, encaminhado para separação farmacêutica.",
    DestinoRoteamento.AUDITORIA_AUTORIZACOES: "Guia de procedimento clínico encaminhada para auditoria de convênio e autorizações TISS/TUSS.",
    DestinoRoteamento.PRONTUARIO_ELETRONICO: "Documento de rotina sem achados críticos arquivado no Prontuário Eletrônico do Paciente.",
}


def destino_por_tipo(tipo_documento: str) -> DestinoRoteamento:
    """Destino de um documento aprovado, pelo tipo: receituário, guia ou, nos demais, o prontuário."""
    if tipo_documento == TipoDocumento.RECEITUARIO.value:
        return DestinoRoteamento.FARMACIA_HOSPITALAR
    if tipo_documento == TipoDocumento.GUIA_SOLICITACAO.value:
        return DestinoRoteamento.AUDITORIA_AUTORIZACOES
    # Laudos de rotina, atestados, relatórios de alta aprovados
    return DestinoRoteamento.PRONTUARIO_ELETRONICO


def gerar_notificacao_urgente(dados: DadosExtraidos, canal_origem: str, aviso: Optional[str] = None) -> NotificacaoGerada:
    """O alerta ao plantão médico de um documento crítico (ou mandado para a urgência por quem revisou)."""
    paciente_nome = dados.paciente.nome if dados.paciente and dados.paciente.nome else "Paciente não identificado"
    diag = dados.diagnostico_principal or "Quadro agudo"
    mensagem = f"ALERTA URGENTE: Laudo critico de {diag} para paciente {paciente_nome} no {canal_origem}."
    if aviso:
        mensagem += f" Atenção: {aviso}"
    return NotificacaoGerada(canal="Alerta_Plantao_Medico", mensagem=mensagem)


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
    achado = classificar_nivel_urgencia(texto_original, dados)
    score_confianca, pendencias = calcular_score_confianca(classificacao, dados, texto_original)
    crm_valido, motivo_crm = validar_crm(dados.medico_solicitante.crm if dados.medico_solicitante else None)

    # Regra 1: Caso Urgente Crítico (Prioridade máxima de atendimento). O CRM ausente ou inválido
    # não atrasa um caso crítico: ele segue para a emergência, com o aviso no alerta e na justificativa.
    if achado.nivel == NivelUrgencia.CRITICO:
        justificativa = _justificativa_critica(achado.termo)
        aviso_crm = None
        if not crm_valido:
            aviso_crm = f"{AVISO_CRM_INVALIDO} ({motivo_crm})."
            justificativa += f" Atenção: {aviso_crm}"
        return DecisaoRoteamento(
            destino_principal=DestinoRoteamento.FILA_EMERGENCIA,
            requer_auditoria_humana=False,
            justificativa_roteamento=justificativa,
            notificacao_gerada=gerar_notificacao_urgente(dados, canal_origem, aviso_crm)
        )

    # Regra 2: CRM ausente ou inválido, termo prioritário ou baixa confiança -> Human-in-the-Loop
    # (Auditoria). O CRM manda para a revisão qualquer que seja o score. Quem revisa pode mandar um
    # prioritário para a urgência pela auditoria.
    motivos = []
    if not crm_valido:
        motivos.append(f"{AVISO_CRM_INVALIDO}: {motivo_crm}.")
    if achado.nivel == NivelUrgencia.PRIORITARIO:
        motivos.append(_justificativa_prioritaria(achado.termo))
    if score_confianca < limiar_auditoria_humana or len(pendencias) >= 2:
        motivo_score = f"Score de confiança baixo ({score_confianca:.2f})."
        outras_inconsistencias = [p for p in pendencias if p != motivo_crm]  # o CRM já foi dito acima
        if outras_inconsistencias:
            motivo_score += f" Inconsistências: {'; '.join(outras_inconsistencias)}."
        motivos.append(motivo_score)
    if motivos:
        return DecisaoRoteamento(
            destino_principal=DestinoRoteamento.FILA_REVISAO_HUMANA,
            requer_auditoria_humana=True,
            justificativa_roteamento=" ".join(motivos) + " Encaminhado para revisão humana.",
            notificacao_gerada=None
        )

    # Regra 3: Roteamento por tipo de documento clínico
    destino = destino_por_tipo(classificacao.tipo_documento)
    return DecisaoRoteamento(
        destino_principal=destino,
        requer_auditoria_humana=False,
        justificativa_roteamento=_JUSTIFICATIVA_POR_DESTINO[destino],
        notificacao_gerada=None
    )
