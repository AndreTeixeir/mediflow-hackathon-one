"""
MediFlow - Configuração compartilhada dos testes (pytest)
Hackathon ONE G10 - Grupo 10: MediFlow

Mapeamento: tests/conftest.py
"""

import pytest

CHAVES_DO_LLM = ("LLM_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")


@pytest.fixture(autouse=True)
def sem_chave_do_llm(monkeypatch):
    """Nenhum teste chama o Gemini de verdade, mesmo com a chave no ambiente ou no .env.

    As variáveis ficam VAZIAS, e não removidas: o código chama load_dotenv(), que só preenche
    o que não existe no ambiente. Com a variável removida, a chave do .env voltaria. Um teste
    que precise simular o Gemini troca o cliente por um mock (ver tests/test_gemini.py).
    """
    for variavel in CHAVES_DO_LLM:
        monkeypatch.setenv(variavel, "")
