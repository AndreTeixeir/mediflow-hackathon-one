"""
MediFlow - Testes do motor determinístico (modo sem IA)
Hackathon ONE G10 - Grupo 10: MediFlow

Responsável: Ingrid Soares
Mapeamento: tests/test_fallback.py
O modo sem IA (sem chave do Gemini, ou depois de uma falha do modelo) não inventa dados: o que
o texto não diz fica vazio e vai para a revisão humana.
Execução (a partir da raiz): python -m pytest tests/test_fallback.py -v
"""

import pytest

from agente import executar_triagem_mediflow
from agente.visao_multimodal import extrair_dados_heuristico_fallback
from contratos.modelos import DadosExtraidos, DestinoRoteamento, DocumentoEntrada, StatusProcessamento, TipoDocumento
from regras.regras_clinicas import UFS_VALIDAS


def receita(trecho_crm: str) -> str:
    return (
        "RECEITUARIO MEDICO. Paciente: Joana Exemplo, 45 anos. "
        f"Solicitante: Dra. Clara Ficticia {trecho_crm}. "
        "Prescricao: 1. Losartana 50mg - 1 comprimido ao dia."
    )


def crm_extraido(texto: str):
    _, dados = extrair_dados_heuristico_fallback(texto)
    return dados.medico_solicitante.crm


# ============================================================================
# CRM: a UF só vem do texto
# ============================================================================

@pytest.mark.parametrize(
    "trecho, esperado",
    [
        ("CRM-SP 112233", "112233-SP"),
        ("CRM/SP 112233", "112233-SP"),
        ("CRM SP 112233", "112233-SP"),
        ("CRM-SP: 112233", "112233-SP"),
        ("CRM 112233-SP", "112233-SP"),
        ("CRM 112233/SP", "112233-SP"),
        ("crm-sp 112233", "112233-SP"),
        ("CRM-MG 112233", "112233-MG"),
        ("CRM 112233", "112233"),
        ("CRM: 112233", "112233"),
        ("CRM-XX 112233", "112233"),
        ("CRM em 112233", "112233"),
    ],
)
def test_crm_so_leva_a_uf_que_esta_no_texto(trecho, esperado):
    assert crm_extraido(receita(trecho)) == esperado


def test_crm_sem_uf_nao_vira_sp():
    assert crm_extraido(receita("CRM 112233")) == "112233"
    assert not crm_extraido(receita("CRM 112233")).endswith("-SP")


@pytest.mark.parametrize("uf", sorted(UFS_VALIDAS))
def test_as_27_ufs_sao_reconhecidas(uf):
    assert crm_extraido(receita(f"CRM-{uf} 123456")) == f"123456-{uf}"


def test_receita_sem_crm_fica_sem_crm():
    assert crm_extraido("RECEITUARIO MEDICO. Paciente: Joana Exemplo, 45 anos. Solicitante: Dra. Clara Ficticia.") is None


def test_crm_fora_do_trecho_do_solicitante_tambem_nao_presume_a_uf():
    texto = "RECEITUARIO MEDICO. Paciente: Joana Exemplo, 45 anos. Dra. Clara Ficticia, CRM 112233. Prescricao: 1. Losartana 50mg."

    assert crm_extraido(texto) == "112233"


# ============================================================================
# Texto sem tipo clínico reconhecido: não identificado, score 0.0, revisão humana
# ============================================================================

TEXTOS_NAO_CLINICOS = [
    "Lista de compras: arroz, feijao e cafe.",
    "Lista de compras: arroz, feijao e cafe. CONCLUSÃO: comprar tudo.",
    "Lista de compras: arroz, feijao e cafe. CRM 1234",
    "Aviso interno: a reuniao de equipe foi remarcada para a proxima semana.",
    "",
    "   \n  ",
]


@pytest.mark.parametrize("texto", TEXTOS_NAO_CLINICOS)
def test_texto_nao_clinico_sai_como_nao_identificado_sem_dados(texto):
    classificacao, dados = extrair_dados_heuristico_fallback(texto)

    assert classificacao.tipo_documento == TipoDocumento.OUTRO_DESCONHECIDO.value
    assert classificacao.score_confianca_classificacao == 0.0
    assert classificacao.especialidade is None
    assert dados == DadosExtraidos()


@pytest.mark.parametrize("texto", [t for t in TEXTOS_NAO_CLINICOS if t.strip()])
def test_texto_nao_clinico_vai_para_a_revisao_humana_no_grafo(texto):
    # Texto vazio nem chega ao grafo: a API o barra com 422.
    entrada = DocumentoEntrada(documento_id="DOC-NAO-CLINICO", documento_texto=texto)

    resposta = executar_triagem_mediflow(entrada)

    assert resposta.classificacao.tipo_documento == TipoDocumento.OUTRO_DESCONHECIDO.value
    assert resposta.classificacao.score_confianca_classificacao == 0.0
    assert resposta.status == StatusProcessamento.PENDENTE_AUDITORIA
    assert resposta.decisao_roteamento.destino_principal == DestinoRoteamento.FILA_REVISAO_HUMANA


@pytest.mark.parametrize(
    "texto, tipo",
    [
        ("RECEITUARIO MEDICO. Paciente: Joana Exemplo, 45 anos.", TipoDocumento.RECEITUARIO),
        ("LAUDO DE EXAME LABORATORIAL. Hemograma completo.", TipoDocumento.LAUDO_EXAME_LABORATORIAL),
        ("LAUDO DE EXAME RADIOLOGICO. Tomografia de torax.", TipoDocumento.LAUDO_EXAME_IMAGEM),
        ("GUIA DE SOLICITACAO DE PROCEDIMENTO TISS.", TipoDocumento.GUIA_SOLICITACAO),
        ("RELATORIO DE ALTA apos internacao de 3 dias.", TipoDocumento.RELATORIO_ALTA),
        ("ATESTADO MEDICO: afastamento por 2 dias.", TipoDocumento.ATESTADO),
    ],
)
def test_tipos_clinicos_reconhecidos_continuam_sendo_classificados(texto, tipo):
    classificacao, _ = extrair_dados_heuristico_fallback(texto)

    assert classificacao.tipo_documento == tipo.value
    assert classificacao.score_confianca_classificacao > 0.0
