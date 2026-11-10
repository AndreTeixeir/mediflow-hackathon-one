"""
MediFlow - Contratos de Dados (Pydantic)
Hackathon ONE G10 - Grupo 10: MediFlow

Responsável: Lethicia
Mapeamento: contratos/modelos.py
Fonte única de verdade para os modelos de entrada, saída, entidades clínicas e OCI.
"""

import os
from datetime import datetime
from typing import List, Optional
from enum import Enum
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

# Carrega variáveis de ambiente do .env caso existam
load_dotenv()


# ============================================================================
# ENUMS / PADRONIZAÇÃO DE VALORES
# ============================================================================

class StatusProcessamento(str, Enum):
    PROCESSADO = "processado"
    PENDENTE_AUDITORIA = "pendente_auditoria"
    REJEITADO = "rejeitado"
    ERRO = "erro"


class TipoDocumento(str, Enum):
    RECEITUARIO = "Receituário Médico"
    LAUDO_EXAME_IMAGEM = "Laudo de Exame de Imagem"
    LAUDO_EXAME_LABORATORIAL = "Laudo de Exame Laboratorial"
    GUIA_SOLICITACAO = "Guia de Solicitação de Procedimento"
    RELATORIO_ALTA = "Relatório de Alta/Internação"
    ATESTADO = "Atestado Médico"
    OUTRO_DESCONHECIDO = "Documento Não Identificado / Ilegível"


class NivelPrioridade(str, Enum):
    ROTINA = "Rotina"
    PRIORITARIO = "Prioritário"
    URGENTE = "Urgente"
    EMERGENCIA = "Emergência"


class DestinoRoteamento(str, Enum):
    FILA_EMERGENCIA = "Fila_Emergencia_Medica"
    AUDITORIA_AUTORIZACOES = "Auditoria_Autorizacoes"
    FARMACIA_HOSPITALAR = "Farmacia_Hospitalar"
    PRONTUARIO_ELETRONICO = "Prontuario_Eletronico_do_Paciente"
    FILA_REVISAO_HUMANA = "Fila_Revisao_Humana"
    # Acrescentado na auditoria humana pela API: o destino de um documento rejeitado por quem revisou.
    # Nenhum valor anterior mudou, e o grafo nunca o produz.
    DOCUMENTO_REJEITADO = "Documento_Rejeitado"


class CanalOrigem(str, Enum):
    PRONTO_SOCORRO = "Pronto_Socorro"
    AMBULATORIO = "Ambulatorio"
    INTERNACAO = "Internacao"
    PORTAL_PACIENTE = "Portal_Paciente"
    SISTEMA_EXTERNO = "Sistema_Externo"


# ============================================================================
# 1. MODELO DE ENTRADA (RECEBIDO PELA API / UPLOAD)
# ============================================================================

class DocumentoEntrada(BaseModel):
    """
    Contrato de dados recebido pela API REST ou tela de upload.
    Compatível com o exemplo oficial do edital do Hackathon (Pág 4).
    """
    documento_id: str = Field(
        ..., 
        description="Identificador único do documento (ex: DOC-CLIN-2026-8942)",
        examples=["DOC-CLIN-2026-8942"]
    )
    tipo_arquivo: str = Field(
        default="PDF",
        description="Extensão/formato do documento (PDF, PNG, JPG, TXT, JSON)",
        examples=["PDF", "PNG", "TXT"]
    )
    documento_texto: Optional[str] = Field(
        default=None,
        description="Texto bruto do documento caso já tenha passado por OCR ou seja digital.",
        examples=[
            "HOSPITAL SANTA LUCIA - LAUDO DE EXAME RADIOLOGICO. Paciente: Carlos Eduardo Mendes, 52 anos. "
            "Solicitante: Dra. Renata Silveira CRM-SP 145892. Exame: Tomografia de Torax com contraste. "
            "Indicacao: Suspeita de embolia pulmonar aguda, dispneia subita. Achados: Falha de enchimento "
            "em arteria pulmonar principal direita compativel com TEP agudo. CONCLUSÃO: Quadro compativel "
            "com Tromboembolismo Pulmonar Agudo. Sugere-se correlacao clinica urgente."
        ]
    )
    documento_base64: Optional[str] = Field(
        default=None,
        description="Conteúdo em Base64 para envio direto de imagem ou PDF digitalizado para a visão multimodal."
    )
    canal_origem: Optional[str] = Field(
        default="Pronto_Socorro",
        description="Canal de entrada do documento hospitalar",
        examples=["Pronto_Socorro", "Ambulatorio", "Internacao"]
    )


# ============================================================================
# 2. SUBMODELOS CLÍNICOS E ESTRUTURADOS
# ============================================================================

class ClassificacaoDocumento(BaseModel):
    tipo_documento: str = Field(
        ...,
        description="Tipo/categoria clínica do documento",
        examples=["Laudo de Exame de Imagem", "Receituário Médico"]
    )
    especialidade: Optional[str] = Field(
        default="Geral",
        description="Especialidade médica associada (ex: Radiologia / Pneumologia, Cardiologia)",
        examples=["Radiologia / Pneumologia"]
    )
    nivel_prioridade: NivelPrioridade = Field(
        default=NivelPrioridade.ROTINA,
        description="Nível de prioridade clínica inferido"
    )
    score_confianca_classificacao: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Score de confiança da LLM na classificação (0.0 a 1.0)",
        examples=[0.99]
    )


class Paciente(BaseModel):
    nome: Optional[str] = Field(None, description="Nome completo do paciente", examples=["Carlos Eduardo Mendes"])
    idade: Optional[int] = Field(None, description="Idade em anos", examples=[52])
    cpf: Optional[str] = Field(None, description="CPF do paciente se houver no documento")
    sexo: Optional[str] = Field(None, description="Sexo/Gênero do paciente (M, F ou Outro)")


class MedicoSolicitante(BaseModel):
    nome: Optional[str] = Field(None, description="Nome do médico solicitante/prescritor", examples=["Dra. Renata Silveira"])
    crm: Optional[str] = Field(None, description="CRM com UF (ex: 145892-SP)", examples=["145892-SP"])


class MedicamentoItem(BaseModel):
    """Estrutura detalhada para prescrições e receituários."""
    nome: str = Field(..., description="Nome do medicamento/princípio ativo", examples=["Losartana Potássica"])
    dosagem: Optional[str] = Field(None, description="Dosagem ou concentração", examples=["50mg"])
    frequencia: Optional[str] = Field(None, description="Posologia/frequência de uso", examples=["1 comprimido de 12 em 12 horas"])
    via_administracao: Optional[str] = Field(None, description="Via de administração (Oral, EV, IM, etc.)", examples=["Oral"])
    duracao: Optional[str] = Field(None, description="Duração do tratamento", examples=["Uso contínuo", "7 dias"])


class DadosExtraidos(BaseModel):
    """
    Conjunto completo de entidades clínicas extraídas do documento.
    """
    paciente: Optional[Paciente] = Field(default_factory=Paciente)
    medico_solicitante: Optional[MedicoSolicitante] = Field(default_factory=MedicoSolicitante)
    exame_realizado: Optional[str] = Field(None, description="Nome do exame clínico/imagem", examples=["Tomografia de Torax com contraste"])
    diagnostico_principal: Optional[str] = Field(None, description="Diagnóstico principal identificado", examples=["Tromboembolismo Pulmonar Agudo (TEP)"])
    cid_sugerido: Optional[str] = Field(None, description="Código CID-10 sugerido", examples=["I26.9"])
    medicamentos: Optional[List[MedicamentoItem]] = Field(default=None, description="Lista de medicamentos prescritos (para receituários)")
    observacoes_clinicas: Optional[str] = Field(None, description="Observações relevantes ou trechos críticos")


class NotificacaoGerada(BaseModel):
    canal: str = Field(..., description="Canal do alerta (ex: Alerta_Plantao_Medico, Webhook_Slack)", examples=["Alerta_Plantao_Medico"])
    mensagem: str = Field(..., description="Texto do alerta gerado para a equipe", examples=["ALERTA URGENTE: Laudo critico de TEP Agudo para paciente Carlos Eduardo Mendes no Pronto-Socorro."])


class DecisaoRoteamento(BaseModel):
    destino_principal: DestinoRoteamento = Field(
        ...,
        description="Fila hospitalar para onde o documento foi roteado",
        examples=[DestinoRoteamento.FILA_EMERGENCIA]
    )
    requer_auditoria_humana: bool = Field(
        ...,
        description="Indica se o caso foi retido para Human-in-the-Loop por baixa confiança ou ambiguidade",
        examples=[False]
    )
    justificativa_roteamento: str = Field(
        ...,
        description="Explicação clínica/operacional para o roteamento",
        examples=["Achado critico de alta gravidade (TEP agudo) detectado em paciente sintomatico."]
    )
    notificacao_gerada: Optional[NotificacaoGerada] = Field(
        default=None,
        description="Dados da notificação imediata, caso seja um evento crítico/urgente"
    )


class ArmazenamentoOCI(BaseModel):
    bucket: str = Field(
        default_factory=lambda: os.getenv("OCI_BUCKET_NAME", "mediflow-documentos-clinicos"),
        description="Nome do bucket no OCI Object Storage Always Free (configurável via OCI_BUCKET_NAME)",
        examples=["mediflow-documentos-clinicos"]
    )
    caminho_objeto: str = Field(
        ...,
        description="Caminho segregado do objeto no bucket (/recebidos, /processados/urgentes, /auditoria_humana)",
        examples=["processados/urgentes/DOC-CLIN-2026-8942.json"]
    )
    status_backup: str = Field(
        default="sucesso",
        description="Status da persistência no OCI Object Storage",
        examples=["sucesso", "pendente", "falha"]
    )


# ============================================================================
# 3. MODELO DE SAÍDA ESTRUTURADA (RESPOSTA FINAL DA API / GRAFO)
# ============================================================================

class TriagemResposta(BaseModel):
    """
    Contrato oficial de saída do MediFlow.
    Idêntico à estrutura exigida no edital do Hackathon (Páginas 4 e 5).
    """
    status: StatusProcessamento = Field(
        default=StatusProcessamento.PROCESSADO,
        description="Status geral do processamento do documento"
    )
    documento_id: str = Field(
        ...,
        description="Identificador original do documento recebido",
        examples=["DOC-CLIN-2026-8942"]
    )
    classificacao: ClassificacaoDocumento
    dados_extraidos: DadosExtraidos
    decisao_roteamento: DecisaoRoteamento
    armazenamento_oci: Optional[ArmazenamentoOCI] = Field(
        default=None,
        description="Metadados da persistência no OCI Object Storage"
    )


# ============================================================================
# 4. MODELO PARA AUDITORIA HUMANA (HUMAN-IN-THE-LOOP)
# ============================================================================

class DecisaoAuditoriaHumana(BaseModel):
    """
    Contrato usado pelo painel Streamlit de auditoria para enviar a decisão humana.
    """
    documento_id: str
    auditor_responsavel: str = Field(..., description="Nome ou ID do auditor clínico", examples=["Lethicia - Auditora"])
    acao: str = Field(..., description="'aprovado' ou 'rejeitado'", examples=["aprovado"])
    motivo_rejeicao: Optional[str] = Field(None, description="Motivo caso rejeitado")
    dados_corrigidos: Optional[DadosExtraidos] = Field(None, description="Dados clínicos após correção manual")
    destino_final: Optional[DestinoRoteamento] = Field(None, description="Novo destino definido pelo auditor")


# ============================================================================
# 5. AUDITORIA HUMANA PELA API (api/auditoria.py)
# Acréscimos: nenhum campo dos modelos acima mudou. A DecisaoAuditoriaHumana da seção 4
# continua aqui; a API usa a DecisaoAuditoriaRequisicao, com os três destinos da fila.
# ============================================================================

class DestinoAuditoria(str, Enum):
    """Para onde quem revisa manda um documento retido: rotina, urgente ou rejeitado."""
    ROTINA = "rotina"
    URGENTE = "urgente"
    REJEITADO = "rejeitado"


class DecisaoAuditoriaRequisicao(BaseModel):
    """Corpo de POST /api/v1/auditoria/{documento_id}/decisao."""
    model_config = ConfigDict(str_strip_whitespace=True)

    destino: DestinoAuditoria = Field(..., description="rotina, urgente ou rejeitado", examples=["urgente"])
    revisor: str = Field(..., min_length=1, max_length=80, description="Nome de quem revisou", examples=["Lethicia"])
    justificativa: str = Field(
        ...,
        min_length=5,
        max_length=500,
        description="Por que quem revisou tomou esta decisão",
        examples=["Dados conferidos com o documento original: quadro agudo."],
    )


class RegistroAuditoriaHumana(BaseModel):
    """
    O registro da decisão humana, gravado dentro do JSON do documento. Guarda também o que o grafo
    tinha decidido, para a trilha mostrar o que a IA decidiu e o que a pessoa mudou.
    """
    revisor: str
    decidido_em: datetime = Field(..., description="Momento da decisão, em UTC (ISO 8601)")
    destino: DestinoAuditoria
    justificativa: str
    destino_original: DestinoRoteamento = Field(..., description="Destino que o grafo tinha decidido")
    justificativa_original: str = Field(..., description="Justificativa do roteamento feita pelo grafo")


class DocumentoAuditado(TriagemResposta):
    """A TriagemResposta do documento depois da decisão humana, com o registro da auditoria."""
    auditoria_humana: RegistroAuditoriaHumana


class ItemPendenteAuditoria(BaseModel):
    """Um documento na fila de GET /api/v1/auditoria/pendentes."""
    documento_id: str
    tipo_documento: str
    nivel_prioridade: NivelPrioridade
    score_confianca_classificacao: float
    justificativa_roteamento: str
    dados_extraidos: DadosExtraidos
    data: datetime = Field(..., description="Quando o documento entrou na fila, em UTC")


class ResumoDocumento(BaseModel):
    """Uma linha de GET /api/v1/documentos."""
    documento_id: str
    bucket: str
    status: StatusProcessamento
    nivel_prioridade: NivelPrioridade
    destino_principal: DestinoRoteamento
    data: datetime = Field(..., description="Quando o documento foi gravado no bucket, em UTC")
