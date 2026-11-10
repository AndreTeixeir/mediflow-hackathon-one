"""
MediFlow - Cache em memória das listagens da auditoria
Hackathon ONE G10 - Grupo 10: MediFlow

Mapeamento: api/cache_listagens.py
Guarda, por um minuto, o resultado de GET /api/v1/auditoria/pendentes e de GET /api/v1/documentos.
Fica fora de api/auditoria.py para api/main.py (a triagem) o invalidar sem import circular.
"""

import threading
import time
from typing import Any, Callable, Dict, Tuple

# Cada GET /api/v1/auditoria/pendentes lista 1 bucket e lê até 50 objetos; cada GET /api/v1/documentos
# lista 4 buckets e lê até 50. No modo OCI, cada listagem e cada leitura é uma requisição ao Object Storage,
# e o Always Free tem 50 mil por mês: uma atualização do painel custaria até ~100, e o painel atualiza a
# cada clique. O cache de 30 s do painel vale por sessão do navegador e não protege a API, que é quem paga.
# Uma triagem e uma decisão registradas limpam o cache (ver api/main.py e api/auditoria.py), então o que a
# pessoa acabou de fazer aparece na hora; só a mudança feita por OUTRA pessoa leva até este tempo.
#
# O cache é em memória e protegido por lock, e só vale porque a API roda com UM worker: com mais de um,
# cada processo teria o seu cache e a invalidação de um não alcançaria os outros.
VALIDADE_DO_CACHE_SEGUNDOS = 60


class CacheDeListagens:
    def __init__(self, validade: float = VALIDADE_DO_CACHE_SEGUNDOS, relogio: Callable[[], float] = time.monotonic):
        # O relógio é injetável: os testes avançam o tempo sem sleep.
        self._validade = validade
        self._relogio = relogio
        self._trava = threading.Lock()
        self._entradas: Dict[str, Tuple[float, Any]] = {}
        # Cada limpar() soma 1. Um resultado calculado numa geração velha não é guardado (ver obter).
        self._geracao = 0

    def obter(self, chave: str, calcular: Callable[[], Any]) -> Any:
        """O valor guardado, se ainda vale; senão, o que `calcular` devolve (que passa a ficar guardado).

        Se `calcular` levantar (por exemplo, o armazenamento falhou e a rota vira 503), nada é guardado.
        O valor devolvido é compartilhado entre as chamadas: quem o recebe não pode alterá-lo.
        """
        with self._trava:
            inicio = self._relogio()
            entrada = self._entradas.get(chave)
            if entrada is not None and inicio - entrada[0] < self._validade:
                return entrada[1]
            geracao = self._geracao

        # As leituras no armazenamento rodam FORA do lock: uma listagem lenta não pode travar o limpar() de
        # uma triagem. O risco disso é o cálculo terminar depois de uma gravação ter limpado o cache e
        # recolocar dados velhos nele; a geração impede isso.
        valor = calcular()

        with self._trava:
            if geracao == self._geracao:
                self._entradas[chave] = (inicio, valor)
        return valor

    def limpar(self) -> None:
        """Esquece tudo e invalida os cálculos que ainda estão em andamento."""
        with self._trava:
            self._entradas.clear()
            self._geracao += 1


LISTAGENS = CacheDeListagens()
