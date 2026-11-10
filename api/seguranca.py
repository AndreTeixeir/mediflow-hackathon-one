"""
MediFlow - Segurança da API: chave de acesso (middleware ASGI)
Hackathon ONE G10 - Grupo 10: MediFlow

Mapeamento: api/seguranca.py
Um middleware ASGI, ligado em api/main.py, que roda ANTES de qualquer rota: uma rota nova nasce
protegida. Sem a chave certa a resposta é 401 e o corpo da requisição nem é lido.

A chave de acesso (X-API-Key, igual a MEDIFLOW_API_KEY) é exigida em todas as requisições, com uma
única exceção, explícita: GET /api/v1/saude, o healthcheck do Docker. A chave nunca aparece em log
nem em resposta.
"""

import hmac
import logging
import os
from dataclasses import dataclass, field

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketClose

logger = logging.getLogger("mediflow.api.seguranca")

CABECALHO_DA_CHAVE = b"x-api-key"  # o servidor entrega os nomes dos cabeçalhos em minúsculas
TAMANHO_MINIMO_DA_CHAVE = 32
# A única rota que responde sem a chave: o healthcheck do Docker (deploy/Dockerfile).
ROTA_PUBLICA = ("GET", "/api/v1/saude")
COMANDO_PARA_GERAR_A_CHAVE = 'python -c "import secrets; print(secrets.token_urlsafe(32))"'


class ConfiguracaoInvalida(Exception):
    """A configuração de segurança não permite subir a API. A mensagem nunca traz a chave."""


@dataclass(frozen=True)
class Configuracao:
    chave: str = field(repr=False)  # nunca em log nem em resposta
    autenticacao_desligada: bool


def ler_configuracao() -> Configuracao:
    """Lê o ambiente a cada chamada. Levanta ConfiguracaoInvalida se a API não puder funcionar com ele."""
    desligada = (os.getenv("MEDIFLOW_API_AUTH") or "").strip().lower() == "desligada"
    chave = (os.getenv("MEDIFLOW_API_KEY") or "").strip()

    if not desligada:
        if not chave:
            raise ConfiguracaoInvalida(
                "MEDIFLOW_API_KEY não está definida: a API não sobe sem chave de acesso. "
                f"Gere uma chave com: {COMANDO_PARA_GERAR_A_CHAVE} e coloque-a no .env como MEDIFLOW_API_KEY. "
                "Só para desenvolvimento, MEDIFLOW_API_AUTH=desligada sobe a API sem chave."
            )
        if len(chave) < TAMANHO_MINIMO_DA_CHAVE:
            raise ConfiguracaoInvalida(
                f"MEDIFLOW_API_KEY tem {len(chave)} caracteres e o mínimo é {TAMANHO_MINIMO_DA_CHAVE}. "
                f"Gere uma chave com: {COMANDO_PARA_GERAR_A_CHAVE}"
            )
    return Configuracao(chave=chave, autenticacao_desligada=desligada)


def _chave_confere(scope: Scope, chave_esperada: str) -> bool:
    for nome, valor in scope["headers"]:
        if nome == CABECALHO_DA_CHAVE:
            # Comparação em tempo constante: o tempo da resposta não revela quantos caracteres batem.
            return hmac.compare_digest(valor, chave_esperada.encode("utf-8"))
    return False


class SegurancaMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            # A API não tem rotas de WebSocket: negar é o que mantém "rota nova nasce protegida".
            await WebSocketClose(code=1008)(scope, receive, send)
            return

        try:
            configuracao = ler_configuracao()
        except ConfiguracaoInvalida:
            # Falha fechada: sem configuração válida nada passa, nem o healthcheck. O motivo vai só para o log.
            logger.error("Configuração de segurança inválida: requisição recusada com 503.")
            await JSONResponse({"detail": "Configuração de segurança da API inválida."}, status_code=503)(
                scope, receive, send
            )
            return

        if (scope["method"], scope["path"]) == ROTA_PUBLICA:
            await self.app(scope, receive, send)
            return

        if not configuracao.autenticacao_desligada and not _chave_confere(scope, configuracao.chave):
            # Sem ler o corpo e sem gravar nada.
            await JSONResponse({"detail": "Chave de acesso ausente ou inválida."}, status_code=401)(
                scope, receive, send
            )
            return

        await self.app(scope, receive, send)
