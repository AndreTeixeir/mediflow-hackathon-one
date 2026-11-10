"""
MediFlow - Testes do contrato (contratos/modelos.py)
Hackathon ONE G10 - Grupo 10: MediFlow

Mapeamento: tests/test_contratos.py
O contrato é da Lethicia e a auditoria humana pela API só ACRESCENTA: estes testes travam o que já
existia (campos e valores dos enums) e conferem o que foi acrescentado.
Execução (a partir da raiz): python -m pytest tests/test_contratos.py -v
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from api.triagem import _bucket_destino
from armazenamento.repositorio import obter_buckets
from contratos import modelos
from contratos.modelos import (
    ClassificacaoDocumento,
    DadosExtraidos,
    DecisaoAuditoriaRequisicao,
    DestinoAuditoria,
    DestinoRoteamento,
    DocumentoAuditado,
    NivelPrioridade,
    RegistroAuditoriaHumana,
    StatusProcessamento,
    TriagemResposta,
)

# Os campos de cada modelo existente, como estavam na main antes da auditoria pela API.
CAMPOS_EXISTENTES = {
    "DocumentoEntrada": ["documento_id", "tipo_arquivo", "documento_texto", "documento_base64", "canal_origem"],
    "ClassificacaoDocumento": ["tipo_documento", "especialidade", "nivel_prioridade", "score_confianca_classificacao"],
    "Paciente": ["nome", "idade", "cpf", "sexo"],
    "MedicoSolicitante": ["nome", "crm"],
    "MedicamentoItem": ["nome", "dosagem", "frequencia", "via_administracao", "duracao"],
    "DadosExtraidos": [
        "paciente", "medico_solicitante", "exame_realizado", "diagnostico_principal",
        "cid_sugerido", "medicamentos", "observacoes_clinicas",
    ],
    "NotificacaoGerada": ["canal", "mensagem"],
    "DecisaoRoteamento": ["destino_principal", "requer_auditoria_humana", "justificativa_roteamento", "notificacao_gerada"],
    "ArmazenamentoOCI": ["bucket", "caminho_objeto", "status_backup"],
    "TriagemResposta": ["status", "documento_id", "classificacao", "dados_extraidos", "decisao_roteamento", "armazenamento_oci"],
    "DecisaoAuditoriaHumana": [
        "documento_id", "auditor_responsavel", "acao", "motivo_rejeicao", "dados_corrigidos", "destino_final",
    ],
}

VALORES_EXISTENTES = {
    "StatusProcessamento": {"PROCESSADO": "processado", "PENDENTE_AUDITORIA": "pendente_auditoria", "REJEITADO": "rejeitado", "ERRO": "erro"},
    "NivelPrioridade": {"ROTINA": "Rotina", "PRIORITARIO": "Prioritário", "URGENTE": "Urgente", "EMERGENCIA": "Emergência"},
    "CanalOrigem": {
        "PRONTO_SOCORRO": "Pronto_Socorro", "AMBULATORIO": "Ambulatorio", "INTERNACAO": "Internacao",
        "PORTAL_PACIENTE": "Portal_Paciente", "SISTEMA_EXTERNO": "Sistema_Externo",
    },
    "TipoDocumento": {
        "RECEITUARIO": "Receituário Médico", "LAUDO_EXAME_IMAGEM": "Laudo de Exame de Imagem",
        "LAUDO_EXAME_LABORATORIAL": "Laudo de Exame Laboratorial", "GUIA_SOLICITACAO": "Guia de Solicitação de Procedimento",
        "RELATORIO_ALTA": "Relatório de Alta/Internação", "ATESTADO": "Atestado Médico",
        "OUTRO_DESCONHECIDO": "Documento Não Identificado / Ilegível",
    },
    "DestinoRoteamento": {
        "FILA_EMERGENCIA": "Fila_Emergencia_Medica", "AUDITORIA_AUTORIZACOES": "Auditoria_Autorizacoes",
        "FARMACIA_HOSPITALAR": "Farmacia_Hospitalar", "PRONTUARIO_ELETRONICO": "Prontuario_Eletronico_do_Paciente",
        "FILA_REVISAO_HUMANA": "Fila_Revisao_Humana",
    },
}


# ============================================================================
# Nada do que existia mudou
# ============================================================================

@pytest.mark.parametrize("nome_do_modelo, campos", CAMPOS_EXISTENTES.items())
def test_os_campos_dos_modelos_existentes_nao_mudaram(nome_do_modelo, campos):
    assert list(getattr(modelos, nome_do_modelo).model_fields) == campos


@pytest.mark.parametrize("nome_do_enum, valores", VALORES_EXISTENTES.items())
def test_os_valores_dos_enums_existentes_nao_mudaram(nome_do_enum, valores):
    enum = getattr(modelos, nome_do_enum)

    for membro, valor in valores.items():
        assert getattr(enum, membro).value == valor


def test_so_o_destino_rejeitado_foi_acrescentado_ao_enum_de_destinos():
    antigos = set(VALORES_EXISTENTES["DestinoRoteamento"].values())
    todos = {destino.value for destino in DestinoRoteamento}

    assert todos - antigos == {"Documento_Rejeitado"}
    assert DestinoRoteamento.DOCUMENTO_REJEITADO.value == "Documento_Rejeitado"
    assert todos >= antigos


def test_os_outros_enums_nao_ganharam_membros():
    for nome_do_enum, valores in VALORES_EXISTENTES.items():
        if nome_do_enum == "DestinoRoteamento":
            continue
        assert {m.value for m in getattr(modelos, nome_do_enum)} == set(valores.values())


@pytest.mark.parametrize("destino", list(DestinoRoteamento))
def test_todo_destino_tem_bucket(destino):
    assert _bucket_destino(destino, obter_buckets())


def test_o_destino_rejeitado_vai_para_o_bucket_de_rejeitados():
    assert _bucket_destino(DestinoRoteamento.DOCUMENTO_REJEITADO, obter_buckets()) == "mediflow-rejeitados"


# ============================================================================
# O que foi acrescentado
# ============================================================================

def triagem_de_exemplo(**extras):
    return dict(
        status="pendente_auditoria",
        documento_id="DOC-1",
        classificacao=dict(tipo_documento="Receituário Médico", score_confianca_classificacao=0.4),
        dados_extraidos={},
        decisao_roteamento=dict(
            destino_principal="Fila_Revisao_Humana", requer_auditoria_humana=True, justificativa_roteamento="Baixa confiança."
        ),
        **extras,
    )


def registro_de_exemplo(**campos):
    base = dict(
        revisor="Lethicia",
        decidido_em=datetime(2026, 11, 10, 15, 30, tzinfo=timezone.utc),
        destino="urgente",
        justificativa="Quadro agudo confirmado.",
        destino_original="Fila_Revisao_Humana",
        justificativa_original="Baixa confiança.",
    )
    base.update(campos)
    return base


def test_documento_auditado_e_uma_triagem_resposta_com_o_registro_da_auditoria():
    documento = DocumentoAuditado(**triagem_de_exemplo(auditoria_humana=registro_de_exemplo()))

    assert isinstance(documento, TriagemResposta)
    assert list(DocumentoAuditado.model_fields) == CAMPOS_EXISTENTES["TriagemResposta"] + ["auditoria_humana"]
    assert documento.auditoria_humana.destino == DestinoAuditoria.URGENTE


def test_o_registro_guarda_o_que_o_grafo_decidiu_e_o_que_a_pessoa_decidiu():
    registro = RegistroAuditoriaHumana(**registro_de_exemplo())

    assert registro.destino_original == DestinoRoteamento.FILA_REVISAO_HUMANA
    assert registro.justificativa_original == "Baixa confiança."
    assert registro.destino == DestinoAuditoria.URGENTE
    assert registro.justificativa == "Quadro agudo confirmado."


def test_a_data_da_decisao_sai_em_iso_8601_utc():
    corpo = RegistroAuditoriaHumana(**registro_de_exemplo()).model_dump(mode="json")

    assert corpo["decidido_em"] == "2026-11-10T15:30:00Z"


def test_decisao_valida_e_aparada():
    decisao = DecisaoAuditoriaRequisicao(destino="rejeitado", revisor="  Lethicia  ", justificativa="  Rasura impede a leitura.  ")

    assert decisao.revisor == "Lethicia"
    assert decisao.justificativa == "Rasura impede a leitura."
    assert decisao.destino == DestinoAuditoria.REJEITADO


@pytest.mark.parametrize("destino", ["rotina", "urgente", "rejeitado"])
def test_os_tres_destinos_da_auditoria_sao_aceitos(destino):
    decisao = DecisaoAuditoriaRequisicao(destino=destino, revisor="Ana", justificativa="Conferido com o original.")

    assert decisao.destino.value == destino


@pytest.mark.parametrize(
    "campos",
    [
        dict(destino="aprovado", revisor="Ana", justificativa="Conferido com o original."),
        dict(destino="Fila_Revisao_Humana", revisor="Ana", justificativa="Conferido com o original."),
        dict(destino="URGENTE", revisor="Ana", justificativa="Conferido com o original."),
        dict(destino="rotina", revisor="", justificativa="Conferido com o original."),
        dict(destino="rotina", revisor="    ", justificativa="Conferido com o original."),
        dict(destino="rotina", revisor="A" * 81, justificativa="Conferido com o original."),
        dict(destino="rotina", revisor="Ana", justificativa="Ok"),
        dict(destino="rotina", revisor="Ana", justificativa="    ok    "),
        dict(destino="rotina", revisor="Ana", justificativa="x" * 501),
        dict(destino="rotina", revisor="Ana"),
        dict(revisor="Ana", justificativa="Conferido com o original."),
        dict(destino="rotina", justificativa="Conferido com o original."),
    ],
)
def test_decisao_invalida_e_recusada(campos):
    with pytest.raises(ValidationError):
        DecisaoAuditoriaRequisicao(**campos)


def test_limites_da_decisao_sao_aceitos():
    decisao = DecisaoAuditoriaRequisicao(destino="rotina", revisor="A" * 80, justificativa="x" * 500)
    minima = DecisaoAuditoriaRequisicao(destino="rotina", revisor="A", justificativa="12345")

    assert len(decisao.revisor) == 80 and len(decisao.justificativa) == 500
    assert minima.revisor == "A" and minima.justificativa == "12345"


def test_o_resumo_e_o_item_pendente_usam_os_enums_do_contrato():
    from contratos.modelos import ItemPendenteAuditoria, ResumoDocumento

    data = datetime(2026, 11, 10, tzinfo=timezone.utc)
    item = ItemPendenteAuditoria(
        documento_id="DOC-1", tipo_documento="Receituário Médico", nivel_prioridade="Prioritário",
        score_confianca_classificacao=0.4, justificativa_roteamento="Baixa confiança.",
        dados_extraidos=DadosExtraidos(), data=data,
    )
    resumo = ResumoDocumento(
        documento_id="DOC-1", bucket="mediflow-rotina", status="processado", nivel_prioridade="Rotina",
        destino_principal="Documento_Rejeitado", data=data,
    )

    assert item.nivel_prioridade == NivelPrioridade.PRIORITARIO
    assert resumo.status == StatusProcessamento.PROCESSADO
    assert resumo.destino_principal == DestinoRoteamento.DOCUMENTO_REJEITADO
