"""
MediFlow - Configuração compartilhada dos testes (pytest)
Hackathon ONE G10 - Grupo 10: MediFlow

Mapeamento: tests/conftest.py
"""

import pytest
from fastapi.testclient import TestClient

CHAVES_DO_LLM = ("LLM_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")
# Só para os testes: 55 caracteres (o mínimo da API é 32).
CHAVE_DE_TESTE = "chave-de-teste-" + "x" * 40


@pytest.fixture(autouse=True)
def sem_chave_do_llm(monkeypatch):
    """Nenhum teste chama o Gemini de verdade, mesmo com a chave no ambiente ou no .env.

    As variáveis ficam VAZIAS, e não removidas: o código chama load_dotenv(), que só preenche
    o que não existe no ambiente. Com a variável removida, a chave do .env voltaria. Um teste
    que precise simular o Gemini troca o cliente por um mock (ver tests/test_gemini.py).
    """
    for variavel in CHAVES_DO_LLM:
        monkeypatch.setenv(variavel, "")


@pytest.fixture(autouse=True)
def seguranca_da_api(monkeypatch):
    """A API exige a chave de acesso também nos testes: eles mandam esta chave, sem desligar a proteção.

    As variáveis de segurança são definidas (as vazias valem o padrão) para o .env de quem roda os testes
    não mudar o resultado, por exemplo com MEDIFLOW_API_AUTH=desligada ou tetos diários diferentes. O
    contador diário é global ao processo: zera antes de cada teste para um não gastar o teto do outro.
    """
    from api.seguranca import CONTADOR_DIARIO

    monkeypatch.setenv("MEDIFLOW_API_KEY", CHAVE_DE_TESTE)
    monkeypatch.setenv("MEDIFLOW_API_AUTH", "")
    monkeypatch.setenv("MEDIFLOW_TETO_DIARIO_DOCUMENTOS", "")
    monkeypatch.setenv("MEDIFLOW_TETO_DIARIO_MB", "")
    CONTADOR_DIARIO.zerar()


@pytest.fixture(autouse=True)
def buckets_com_o_prefixo_padrao(monkeypatch):
    """Os testes esperam os buckets mediflow-*: o OCI_BUCKET_PREFIXO do .env de quem roda (a equipe pode usar
    outro) não pode mudar os nomes. Vazio vale o padrão e, vazio, o load_dotenv() não o preenche de volta."""
    monkeypatch.setenv("OCI_BUCKET_PREFIXO", "")


@pytest.fixture
def chave_de_teste():
    return CHAVE_DE_TESTE


@pytest.fixture
def novo_cliente(chave_de_teste):
    """Fábrica de TestClient da API que já manda a chave de acesso. Aceita as opções do TestClient."""
    from api.main import app

    def criar(**opcoes):
        return TestClient(app, headers={"X-API-Key": chave_de_teste}, **opcoes)

    return criar
