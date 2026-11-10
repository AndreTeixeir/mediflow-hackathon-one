"""
MediFlow - Testes da chamada ao Gemini (modelo, novas tentativas e log)
Hackathon ONE G10 - Grupo 10: MediFlow

Responsável: Ingrid Soares
Mapeamento: tests/test_gemini.py
Nenhum teste chama o Gemini de verdade: o cliente do SDK é substituído por um falso.
Execução (a partir da raiz): python -m pytest tests/test_gemini.py -v
"""

import pytest

from agente.visao_multimodal import MODELO_GEMINI_DEFAULT, resolver_modelo


# ============================================================================
# Modelo padrão
# ============================================================================

def test_o_modelo_padrao_e_o_gemini_3_5_flash_lite():
    assert MODELO_GEMINI_DEFAULT == "gemini-3.5-flash-lite"


@pytest.mark.parametrize(
    "valor_do_ambiente, esperado",
    [
        (None, MODELO_GEMINI_DEFAULT),
        ("", MODELO_GEMINI_DEFAULT),
        ("   ", MODELO_GEMINI_DEFAULT),
        ("gemini-outro", "gemini-outro"),
        ("  gemini-outro  ", "gemini-outro"),
    ],
)
def test_resolver_modelo_usa_o_ambiente_ou_o_padrao(valor_do_ambiente, esperado):
    assert resolver_modelo(valor_do_ambiente) == esperado
