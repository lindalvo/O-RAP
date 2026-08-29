"""Inicialização da gNB e das O-RUs da topologia experimental.

Reaproveita as definições globais compartilhadas em common.afinidades e
common.comandos, mantendo a lógica específica do pipeline.
"""

from __future__ import annotations

import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from common.afinidades import GNB_CPUSETS, GNB_NUMA, RU_CPUSETS, RU_NUMA
from common.comandos import iniciar
from configuracoes import Topologia
from erros import ErroProcesso
from common.constantes import HOST_METRICAS,PORTA_METRICAS,TIMEOUT_METRICAS_SEGUNDOS,INTERVALO_TENTATIVAS_SEGUNDOS,ESTABILIZACAO_SEGUNDOS

class ArquivosConfiguracao(Protocol):
    """Arquivos gerados pelo futuro módulo ``yamls``."""

    diretorio: Path
    gnb_yaml: Path
    rus_yaml: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class ProcessosAtivos:
    """Processos iniciados para uma topologia."""

    gnb: subprocess.Popen
    rus: tuple[subprocess.Popen, ...]


def aguardar_porta_metricas(
    processo_gnb: subprocess.Popen | None = None,
) -> None:
    """Aguarda a gNB disponibilizar a porta TCP de métricas."""
    limite = time.monotonic() + TIMEOUT_METRICAS_SEGUNDOS

    while time.monotonic() < limite:
        if processo_gnb is not None and processo_gnb.poll() is not None:
            raise ErroProcesso(
                "A gNB encerrou antes de disponibilizar a porta de métricas "
                f"(retorno={processo_gnb.returncode})."
            )

        try:
            with socket.create_connection((HOST_METRICAS, PORTA_METRICAS), timeout=1):
                print(
                    f"Serviço de métricas disponível em {HOST_METRICAS}:{PORTA_METRICAS}.",
                    flush=True,
                )
                return
        except OSError:
            restante = limite - time.monotonic()
            if restante > 0:
                time.sleep(min(INTERVALO_TENTATIVAS_SEGUNDOS, restante))

    raise ErroProcesso(
        f"Serviço de métricas indisponível em {HOST_METRICAS}:{PORTA_METRICAS} "
        f"após {TIMEOUT_METRICAS_SEGUNDOS:g} segundos."
    )


def _comando_gnb(topologia: Topologia, gnb_yaml: Path) -> list[str]:
    return [
        "numactl",
        f"--cpunodebind={GNB_NUMA}",
        f"--membind={GNB_NUMA}",
        "taskset",
        "-c",
        GNB_CPUSETS[topologia.quantidade_rus],
        "gnb",
        "-c",
        str(gnb_yaml),
    ]


def _comando_ru(indice: int, cpuset: str, ru_yaml: Path) -> list[str]:
    return [
        "ip",
        "netns",
        "exec",
        f"ru{indice}",
        "numactl",
        f"--cpunodebind={RU_NUMA}",
        f"--membind={RU_NUMA}",
        "taskset",
        "-c",
        cpuset,
        "ru_emulator",
        "-c",
        str(ru_yaml),
    ]


def iniciar_gnb_e_rus(
    topologia: Topologia,
    arquivos: ArquivosConfiguracao,
) -> ProcessosAtivos:
    """Inicia a gNB, aguarda suas métricas e inicia todas as O-RUs."""
    try:
        processo_gnb = iniciar(
            _comando_gnb(topologia, arquivos.gnb_yaml),
            arquivos.diretorio / "gnb.out",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ErroProcesso(f"Não foi possível iniciar a gNB: {exc}") from exc

    processos_rus: list[subprocess.Popen] = []

    try:
        aguardar_porta_metricas(processo_gnb=processo_gnb)

        cpus_rus = RU_CPUSETS[topologia.quantidade_rus]
        for indice, (ru_yaml, cpuset) in enumerate(
            zip(arquivos.rus_yaml, cpus_rus, strict=True),
            start=1,
        ):
            processos_rus.append(
                iniciar(
                    _comando_ru(indice, cpuset, ru_yaml),
                    arquivos.diretorio / f"ru{indice}.out",
                )
            )

        print(
            f"Aguardando {ESTABILIZACAO_SEGUNDOS}s para estabilização...",
            flush=True,
        )
        time.sleep(ESTABILIZACAO_SEGUNDOS)

        if processo_gnb.poll() is not None:
            raise ErroProcesso(
                "A gNB encerrou durante a estabilização "
                f"(retorno={processo_gnb.returncode})."
            )

        for indice, processo_ru in enumerate(processos_rus, start=1):
            if processo_ru.poll() is not None:
                raise ErroProcesso(
                    f"A O-RU {indice} encerrou durante a estabilização "
                    f"(retorno={processo_ru.returncode})."
                )

    except Exception as exc:
        from encerramento import encerrar_processos

        encerrar_processos(
            ProcessosAtivos(processo_gnb, tuple(processos_rus))
        )

        if isinstance(exc, ErroProcesso):
            raise
        raise ErroProcesso(
            f"Falha ao iniciar {topologia.cenario}/"
            f"{topologia.identificador}: {exc}"
        ) from exc

    return ProcessosAtivos(processo_gnb, tuple(processos_rus))
