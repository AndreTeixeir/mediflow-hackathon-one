"""
MediFlow - Dependências compartilhadas da API
Hackathon ONE G10 - Grupo 10: MediFlow

Mapeamento: api/dependencias.py
Fica fora de api/main.py para os roteadores (api/auditoria.py) a usarem sem import circular.
"""

from functools import lru_cache

from armazenamento.repositorio import RepositorioDocumentos, criar_repositorio


@lru_cache(maxsize=1)
def obter_repositorio() -> RepositorioDocumentos:
    """Criado uma vez, no primeiro uso. Os testes o substituem com app.dependency_overrides."""
    return criar_repositorio()
