"""Execução padronizada dos comandos externos do pipeline."""

from __future__ import annotations

import subprocess
from pathlib import Path
from shlex import join, quote


def executar(
    comando: list[str],
    *,
    verificar: bool = True,
    ocultar_stderr: bool = False,
) -> subprocess.CompletedProcess:
    """Executa um comando síncrono após imprimi-lo no log."""
    print(f"+ {join(comando)}", flush=True)
    return subprocess.run(
        comando,
        check=verificar,
        stderr=subprocess.DEVNULL if ocultar_stderr else None,
    )


def iniciar(
    comando: list[str],
    arquivo_saida: Path,
) -> subprocess.Popen:
    """Inicia um processo em nova sessão e redireciona sua saída."""
    print(
        f"+ {join(comando)} >> {quote(str(arquivo_saida))} 2>&1 &",
        flush=True,
    )
    with arquivo_saida.open("ab") as saida:
        return subprocess.Popen(
            comando,
            stdout=saida,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
