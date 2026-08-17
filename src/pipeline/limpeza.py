"""Limpeza inicial de resíduos deixados por uma execução interrompida."""

from __future__ import annotations

import time

from comandos import executar
from configuracoes import MAXIMO_RUS


ESPERA_ENCERRAMENTO_SEGUNDOS = 2


def _executar_tolerando_ausencia(comando: list[str]) -> None:
    executar(comando, verificar=False, ocultar_stderr=True)


def limpar_ambiente_residual() -> None:
    """Encerra processos residuais e remove topologias de ru1 a ru5.

    Esta rotina pressupõe o servidor dedicado da campanha, sem outras
    instâncias legítimas de gNB ou ru_emulator.
    """
    print("\n=== Limpeza inicial de resíduos ===", flush=True)

    _executar_tolerando_ausencia(["pkill", "-TERM", "-x", "ru_emulator"])
    _executar_tolerando_ausencia(["pkill", "-TERM", "-x", "gnb"])
    time.sleep(ESPERA_ENCERRAMENTO_SEGUNDOS)

    _executar_tolerando_ausencia(["pkill", "-KILL", "-x", "ru_emulator"])
    _executar_tolerando_ausencia(["pkill", "-KILL", "-x", "gnb"])

    for indice_ru in reversed(range(1, MAXIMO_RUS + 1)):
        namespace = f"ru{indice_ru}"
        interface_du = f"ofh_du{indice_ru}"

        _executar_tolerando_ausencia(
            ["tc", "qdisc", "del", "dev", interface_du, "root"]
        )
        _executar_tolerando_ausencia(
            ["ip", "netns", "del", namespace]
        )
        _executar_tolerando_ausencia(
            ["ip", "link", "del", interface_du]
        )
