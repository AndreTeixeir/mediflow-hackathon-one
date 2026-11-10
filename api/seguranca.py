"""
MediFlow - Segurança da API: chave de acesso, limite de tamanho e tetos diários (middleware ASGI)
Hackathon ONE G10 - Grupo 10: MediFlow

Mapeamento: api/seguranca.py
Um middleware ASGI, ligado em api/main.py, que roda ANTES de qualquer rota: uma rota nova nasce
protegida. Sem a chave certa a resposta é 401 e o corpo da requisição nem é lido.

A chave de acesso (X-API-Key, igual a MEDIFLOW_API_KEY) é exigida em todas as requisições, com uma
única exceção, explícita: GET /api/v1/saude, o healthcheck do Docker. A chave nunca aparece em log
nem em resposta.

O corpo da requisição tem no máximo 15 MB, o mesmo `client_max_body_size 15M` do nginx: acima disso,
413. O middleware lê o corpo com esse teto e o repassa à rota em memória.

Dois tetos por dia protegem a cota do Gemini e o armazenamento: MEDIFLOW_TETO_DIARIO_DOCUMENTOS (padrão
100) e MEDIFLOW_TETO_DIARIO_MB (padrão 300). Cada POST /api/v1/triagem que passou pela chave e pelo
limite de tamanho soma 1 documento e o tamanho do corpo. Ao atingir um teto, 429, sem gravar nada e sem
chamar o Gemini. A contagem fica em memória, zera à meia-noite de America/Sao_Paulo e quando a API reinicia.
"""

import hmac
import logging
import math
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Callable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketClose

logger = logging.getLogger("mediflow.api.seguranca")

CABECALHO_DA_CHAVE = b"x-api-key"  # o servidor entrega os nomes dos cabeçalhos em minúsculas
TAMANHO_MINIMO_DA_CHAVE = 32
# O mesmo limite do nginx (`client_max_body_size 15M`, em MiB).
UM_MB = 1024 * 1024
LIMITE_DO_CORPO_EM_BYTES = 15 * UM_MB
TETO_DIARIO_PADRAO_DE_DOCUMENTOS = 100
TETO_DIARIO_PADRAO_DE_MB = 300
ROTA_DA_TRIAGEM = ("POST", "/api/v1/triagem")
MENSAGEM_429_DOCUMENTOS = "Limite diário de documentos atingido. Tente novamente amanhã."
MENSAGEM_429_MB = "Limite diário de MB atingido. Tente novamente amanhã."
# A única rota que responde sem a chave: o healthcheck do Docker (deploy/Dockerfile).
ROTA_PUBLICA = ("GET", "/api/v1/saude")
COMANDO_PARA_GERAR_A_CHAVE = 'python -c "import secrets; print(secrets.token_urlsafe(32))"'


class ConfiguracaoInvalida(Exception):
    """A configuração de segurança não permite subir a API. A mensagem nunca traz a chave."""


@dataclass(frozen=True)
class Configuracao:
    chave: str = field(repr=False)  # nunca em log nem em resposta
    autenticacao_desligada: bool
    teto_diario_de_documentos: int
    teto_diario_de_bytes: int


def _ler_teto(nome: str, padrao, converter):
    """O teto de uma variável de ambiente: vazia vale o padrão; só vale um número finito maior que zero."""
    bruto = (os.getenv(nome) or "").strip()
    if not bruto:
        return padrao
    try:
        valor = converter(bruto)
    except ValueError:
        valor = None
    if valor is None or not math.isfinite(valor) or valor <= 0:
        raise ConfiguracaoInvalida(
            f"{nome} precisa ser um número maior que zero (recebeu '{bruto[:40]}'). "
            f"Deixe vazia para usar o padrão ({padrao})."
        )
    return valor


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
    teto_de_documentos = _ler_teto("MEDIFLOW_TETO_DIARIO_DOCUMENTOS", TETO_DIARIO_PADRAO_DE_DOCUMENTOS, int)
    teto_de_mb = _ler_teto("MEDIFLOW_TETO_DIARIO_MB", TETO_DIARIO_PADRAO_DE_MB, float)
    return Configuracao(
        chave=chave,
        autenticacao_desligada=desligada,
        teto_diario_de_documentos=teto_de_documentos,
        teto_diario_de_bytes=int(teto_de_mb * UM_MB),
    )


def _fuso_de_sao_paulo() -> tzinfo:
    try:
        return ZoneInfo("America/Sao_Paulo")
    except ZoneInfoNotFoundError:
        # Sem a base de fusos do sistema, UTC-3 fixo dá o mesmo dia: o Brasil não tem horário de verão desde 2019.
        logger.warning("Base de fusos horários ausente: os tetos diários viram o dia em UTC-3 fixo.")
        return timezone(timedelta(hours=-3))


class ContadorDiario:
    """Quantos documentos e quantos bytes a triagem recebeu hoje. Em memória, protegido por lock, zerado
    à meia-noite de America/Sao_Paulo (e quando a API reinicia, porque nasce vazio)."""

    def __init__(self, relogio: Optional[Callable[[], datetime]] = None):
        # O relógio devolve um datetime com fuso; os testes o injetam, sem sleep.
        self._relogio = relogio or (lambda: datetime.now(timezone.utc))
        self._fuso = _fuso_de_sao_paulo()
        self._trava = threading.Lock()
        self._dia = None
        self._documentos = 0
        self._bytes = 0

    def _dia_de_hoje(self):
        return self._relogio().astimezone(self._fuso).date()

    def tentar_registrar(self, tamanho: int, teto_de_documentos: int, teto_de_bytes: int) -> Optional[str]:
        """Conta o documento e devolve None, ou devolve o teto atingido ("documentos" ou "mb") sem contar.

        O teto nunca é ultrapassado: o documento só entra se este e o volume dele ainda couberem."""
        with self._trava:
            hoje = self._dia_de_hoje()
            if hoje != self._dia:
                self._dia, self._documentos, self._bytes = hoje, 0, 0
            if self._documentos + 1 > teto_de_documentos:
                return "documentos"
            if self._bytes + tamanho > teto_de_bytes:
                return "mb"
            self._documentos += 1
            self._bytes += tamanho
            return None

    def zerar(self) -> None:
        with self._trava:
            self._dia, self._documentos, self._bytes = None, 0, 0


CONTADOR_DIARIO = ContadorDiario()


def _chave_confere(scope: Scope, chave_esperada: str) -> bool:
    for nome, valor in scope["headers"]:
        if nome == CABECALHO_DA_CHAVE:
            # Comparação em tempo constante: o tempo da resposta não revela quantos caracteres batem.
            return hmac.compare_digest(valor, chave_esperada.encode("utf-8"))
    return False


class CorpoGrandeDemais(Exception):
    """O corpo passou do limite enquanto era lido."""


class ClienteDesconectou(Exception):
    """O cliente fechou a conexão antes de terminar de enviar o corpo."""


def _tamanho_declarado(scope: Scope) -> Optional[int]:
    for nome, valor in scope["headers"]:
        if nome == b"content-length":
            try:
                return int(valor)
            except ValueError:
                return None  # inválido: vale como ausente, e o corpo é lido com o teto de qualquer jeito
    return None


async def _ler_corpo_com_teto(receive: Receive) -> bytes:
    """Lê o corpo inteiro e para assim que passar do limite, sem confiar no Content-Length."""
    partes = []
    total = 0
    while True:
        mensagem = await receive()
        if mensagem["type"] == "http.disconnect":
            raise ClienteDesconectou()
        pedaco = mensagem.get("body", b"")
        total += len(pedaco)
        if total > LIMITE_DO_CORPO_EM_BYTES:
            raise CorpoGrandeDemais()
        partes.append(pedaco)
        if not mensagem.get("more_body", False):
            return b"".join(partes)


def _repassar_corpo(corpo: bytes, receive: Receive) -> Receive:
    """O `receive` da rota: entrega o corpo já lido, em memória, e depois volta ao canal original
    (que só traz o aviso de que o cliente desconectou)."""
    entregue = False

    async def receber() -> dict:
        nonlocal entregue
        if not entregue:
            entregue = True
            return {"type": "http.request", "body": corpo, "more_body": False}
        return await receive()

    return receber


async def _recusar_corpo_grande(scope: Scope, receive: Receive, send: Send) -> None:
    await JSONResponse({"detail": "Corpo da requisição acima do limite de 15 MB."}, status_code=413)(
        scope, receive, send
    )


class SegurancaMiddleware:
    def __init__(self, app: ASGIApp, contador: Optional[ContadorDiario] = None):
        self.app = app
        self.contador = contador or CONTADOR_DIARIO

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

        # Content-Length acima do limite: 413 antes de ler uma linha do corpo.
        declarado = _tamanho_declarado(scope)
        if declarado is not None and declarado > LIMITE_DO_CORPO_EM_BYTES:
            await _recusar_corpo_grande(scope, receive, send)
            return

        # Sem Content-Length (ou com um que mente), o corpo é lido com teto e cortado em 413 ao passar do limite.
        try:
            corpo = await _ler_corpo_com_teto(receive)
        except CorpoGrandeDemais:
            await _recusar_corpo_grande(scope, receive, send)
            return
        except ClienteDesconectou:
            return

        # Tetos diários: só a triagem conta, depois da chave e do limite de tamanho. Acima do teto, 429 sem
        # chegar à rota: nada é gravado e o Gemini não é chamado.
        if (scope["method"], scope["path"]) == ROTA_DA_TRIAGEM:
            teto_atingido = self.contador.tentar_registrar(
                len(corpo), configuracao.teto_diario_de_documentos, configuracao.teto_diario_de_bytes
            )
            if teto_atingido:
                mensagem = MENSAGEM_429_DOCUMENTOS if teto_atingido == "documentos" else MENSAGEM_429_MB
                await JSONResponse({"detail": mensagem}, status_code=429)(scope, receive, send)
                return

        await self.app(scope, _repassar_corpo(corpo, receive), send)
