"""Comandos para encerrar a gNB e as O-RUs da configuração atual."""

from __future__ import annotations

import os
import signal
import subprocess
import time

from processos import ProcessosAtivos


TEMPO_ENCERRAMENTO_SEGUNDOS = 5


def _sinalizar(processo, sinal: signal.Signals) -> None:
    if processo.poll() is not None:
        return

    print(f"+ kill -{sinal.name.removeprefix('SIG')} -- -{processo.pid}")
    try:
        os.killpg(processo.pid, sinal)
    except ProcessLookupError:
        pass


def encerrar_processos(processos: ProcessosAtivos) -> None:
    """Encerra grupos com SIGTERM e força SIGKILL quando necessário."""
    print("\n=== Encerramento dos processos ===")
    todos = [*reversed(processos.rus), processos.gnb]

    for processo in todos:
        _sinalizar(processo, signal.SIGTERM)

    limite = time.monotonic() + TEMPO_ENCERRAMENTO_SEGUNDOS
    while any(p.poll() is None for p in todos) and time.monotonic() < limite:
        time.sleep(0.1)

    for processo in todos:
        _sinalizar(processo, signal.SIGKILL)

    for processo in todos:
        try:
            processo.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
