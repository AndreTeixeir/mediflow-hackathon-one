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

from agente.visao_multimodal import extrair_dados_heuristico_fallback
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
