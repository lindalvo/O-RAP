"""Encerramento da gNB e das O-RUs da topologia atual."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from typing import Protocol


TEMPO_ENCERRAMENTO_SEGUNDOS = 5
TIMEOUT_WAIT_SEGUNDOS = 2


class ProcessosEncerraveis(Protocol):
    """Estrutura mínima retornada pelo módulo de processos."""

    gnb: subprocess.Popen
    rus: tuple[subprocess.Popen, ...]


def _sinalizar(
    processo: subprocess.Popen,
    sinal: signal.Signals,
) -> None:
    """Envia um sinal ao grupo do processo caso ele ainda esteja ativo."""
    if processo.poll() is not None:
        return

    print(
        f"+ kill -{sinal.name.removeprefix('SIG')} -- -{processo.pid}",
        flush=True,
    )
    try:
        os.killpg(processo.pid, sinal)
    except ProcessLookupError:
        pass


def encerrar_processos(processos: ProcessosEncerraveis) -> None:
    """Encerra os grupos com SIGTERM e força SIGKILL quando necessário."""
    print("\n=== Encerramento dos processos ===", flush=True)

    # As O-RUs são encerradas primeiro e em ordem inversa; a gNB é a última.
    todos = [*reversed(processos.rus), processos.gnb]

    for processo in todos:
        _sinalizar(processo, signal.SIGTERM)

    limite = time.monotonic() + TEMPO_ENCERRAMENTO_SEGUNDOS
    while (
        any(processo.poll() is None for processo in todos)
        and time.monotonic() < limite
    ):
        time.sleep(0.1)

    for processo in todos:
        _sinalizar(processo, signal.SIGKILL)

    for processo in todos:
        try:
            processo.wait(timeout=TIMEOUT_WAIT_SEGUNDOS)
        except subprocess.TimeoutExpired:
            pass
