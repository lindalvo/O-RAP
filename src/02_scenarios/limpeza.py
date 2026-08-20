"""Limpeza inicial de resíduos deixados por uma execução interrompida."""

from __future__ import annotations

import time

from comandos import executar
from rede import remover_topologia


MAXIMO_RUS = 5
ESPERA_ENCERRAMENTO_SEGUNDOS = 2


def _executar_tolerando_ausencia(comando: list[str]) -> None:
    """Executa um comando de limpeza sem falhar se nada for encontrado."""
    executar(comando, verificar=False, ocultar_stderr=True)


def limpar_ambiente_residual() -> None:
    """Encerra processos residuais e remove redes de ``ru1`` a ``ru5``.

    Esta rotina pressupõe que o servidor é dedicado exclusivamente à campanha.
    """
    print("\n=== Limpeza inicial de resíduos ===", flush=True)

    _executar_tolerando_ausencia(["pkill", "-TERM", "-x", "ru_emulator"])
    _executar_tolerando_ausencia(["pkill", "-TERM", "-x", "gnb"])
    time.sleep(ESPERA_ENCERRAMENTO_SEGUNDOS)

    _executar_tolerando_ausencia(["pkill", "-KILL", "-x", "ru_emulator"])
    _executar_tolerando_ausencia(["pkill", "-KILL", "-x", "gnb"])

    remover_topologia(
        MAXIMO_RUS,
        titulo="Remoção de topologias residuais",
    )
