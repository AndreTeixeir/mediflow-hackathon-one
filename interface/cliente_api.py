"""
MediFlow - Cliente HTTP da API para o painel Streamlit
Hackathon ONE G10 - Grupo 10: MediFlow

Mapeamento: interface/cliente_api.py
Uma função só faz todas as chamadas do painel à API (com a chave de acesso) e traduz cada falha numa
mensagem para quem usa a tela. Não importa o Streamlit: dá para testar sozinho. Nada é simulado: se a
API não responder 200, levanta ErroDaApi e quem chamou mostra a mensagem.
"""

import os
from typing import Any, Dict, Optional

import requests

URL_PADRAO_DA_API = "http://localhost:8000"
TIMEOUT_PADRAO_SEGUNDOS = 30
# O pior caso da triagem são 3 chamadas ao Gemini de até 25 s mais as esperas (81 s): o prazo fica acima.
TIMEOUT_DA_TRIAGEM_SEGUNDOS = 90

MENSAGENS_POR_STATUS = {
    401: "Acesso negado (HTTP 401): a chave de acesso da API está ausente ou inválida. "
         "Confira a variável MEDIFLOW_API_KEY do painel.",
    404: "Não encontrado (HTTP 404).",
    409: "Conflito (HTTP 409).",
    413: "O envio é grande demais para a API (HTTP 413). O limite é de 15 MB.",
    422: "A API recusou os dados enviados (HTTP 422).",
    429: "Limite diário da API atingido (HTTP 429).",
    503: "A API ou o armazenamento está indisponível (HTTP 503). Tente de novo em instantes.",
}


class ErroDaApi(Exception):
    """A chamada à API não deu certo. `mensagem` é para mostrar na tela."""

    def __init__(
        self,
        mensagem: str,
        status: Optional[int] = None,
        corpo: Any = None,
        endereco: Optional[str] = None,
    ):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.status = status  # o código HTTP, quando a API respondeu
        self.corpo = corpo  # o JSON da resposta de erro, quando veio um
        self.endereco = endereco  # o endereço tentado, quando não foi possível conectar


def cabecalhos_da_api() -> Dict[str, str]:
    """O X-API-Key com MEDIFLOW_API_KEY do ambiente, em toda chamada. Sem a variável, nenhum cabeçalho: a API
    responde 401 e a tela diz que a chave está ausente. A chave nunca vai para uma mensagem."""
    chave = (os.getenv("MEDIFLOW_API_KEY") or "").strip()
    return {"X-API-Key": chave} if chave else {}


def url_da_api() -> str:
    """MEDIFLOW_API_URL, lida a cada chamada. Vazia, vale o endereço local."""
    return os.getenv("MEDIFLOW_API_URL", URL_PADRAO_DA_API).strip().rstrip("/") or URL_PADRAO_DA_API


def chamar_api(
    metodo: str,
    caminho: str,
    *,
    json: Any = None,
    timeout: float = TIMEOUT_PADRAO_SEGUNDOS,
    mensagens: Optional[Dict[int, str]] = None,
) -> Any:
    """Chama a API e devolve o JSON da resposta 200. `mensagens` troca o texto de um código específico."""
    endereco = f"{url_da_api()}{caminho}"
    try:
        resposta = requests.request(metodo, endereco, json=json, timeout=timeout, headers=cabecalhos_da_api())
    except requests.exceptions.ConnectionError:
        raise ErroDaApi("Não foi possível conectar à API.", endereco=endereco) from None
    except requests.exceptions.Timeout:
        raise ErroDaApi("A API demorou demais para responder. Tente novamente ou verifique os logs do back-end.") from None
    except requests.exceptions.RequestException as erro:
        # Só o tipo: a mensagem de uma exceção de rede pode trazer o endereço interno ou credenciais.
        raise ErroDaApi(f"Erro na comunicação com a API ({type(erro).__name__}).") from None

    try:
        corpo = resposta.json()
    except ValueError:
        corpo = None

    if resposta.status_code == 200:
        if corpo is None:
            raise ErroDaApi("A resposta da API não veio em JSON válido.", status=200)
        return corpo

    personalizada = (mensagens or {}).get(resposta.status_code)
    mensagem = personalizada or MENSAGENS_POR_STATUS.get(
        resposta.status_code, f"A API respondeu com HTTP {resposta.status_code}."
    )
    detalhe = corpo.get("detail") if isinstance(corpo, dict) else None
    if not personalizada and isinstance(detalhe, str) and detalhe:
        mensagem += f" {detalhe}"
    raise ErroDaApi(mensagem, status=resposta.status_code, corpo=corpo)
