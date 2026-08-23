"""Impressão dos comandos de inicialização da gNB e das O-RUs."""

from __future__ import annotations

import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from common.afinidades import GNB_CPUSETS, GNB_NUMA, RU_CPUSETS, RU_NUMA
from common.comandos import iniciar
from configuracoes import Configuracao
from yamls import ArquivosConfiguracao


HOST_METRICAS = "127.0.0.1"
PORTA_METRICAS = 8001
TIMEOUT_METRICAS_SEGUNDOS = 60
INTERVALO_TENTATIVAS_SEGUNDOS = 1
ESTABILIZACAO_SEGUNDOS = 7


@dataclass(frozen=True)
class ProcessosAtivos:
    gnb: subprocess.Popen
    rus: tuple[subprocess.Popen, ...]


def aguardar_porta_metricas(
    host: str = HOST_METRICAS,
    porta: int = PORTA_METRICAS,
    timeout_segundos: float = TIMEOUT_METRICAS_SEGUNDOS,
    intervalo_segundos: float = INTERVALO_TENTATIVAS_SEGUNDOS,
    processo_gnb: subprocess.Popen | None = None,
) -> None:
    """Aguarda uma conexão TCP com o serviço de métricas da gNB."""
    limite = time.monotonic() + timeout_segundos

    while time.monotonic() < limite:
        if processo_gnb is not None and processo_gnb.poll() is not None:
            raise RuntimeError(
                "A gNB encerrou antes de disponibilizar a porta de métricas "
                f"(retorno={processo_gnb.returncode})."
            )
        try:
            with socket.create_connection((host, porta), timeout=1):
                print(f"Serviço de métricas disponível em {host}:{porta}.")
                return
        except OSError:
            restante = limite - time.monotonic()
            if restante > 0:
                time.sleep(min(intervalo_segundos, restante))

    raise TimeoutError(
        f"Serviço de métricas indisponível em {host}:{porta} "
        f"após {timeout_segundos:g} segundos."
    )


def iniciar_gnb_e_rus(
    configuracao: Configuracao,
    arquivos: ArquivosConfiguracao,
) -> ProcessosAtivos:
    """Inicia gNB, aguarda métricas e inicia as O-RUs."""
    comando_gnb = [
        "numactl",
        f"--cpunodebind={GNB_NUMA}",
        f"--membind={GNB_NUMA}",
        "taskset",
        "-c",
        GNB_CPUSETS[configuracao.quantidade_rus],
        "gnb",
        "-c",
        str(arquivos.gnb_yaml),
    ]
    processo_gnb = iniciar(
        comando_gnb,
        arquivos.diretorio / "gnb.out",
    )
    processos_rus: list[subprocess.Popen] = []

    try:
        aguardar_porta_metricas(processo_gnb=processo_gnb)

        cpus_rus = RU_CPUSETS[configuracao.quantidade_rus]
        for indice, (ru_yaml, cpuset) in enumerate(
            zip(arquivos.rus_yaml, cpus_rus, strict=True),
            start=1,
        ):
            comando_ru = [
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
            processos_rus.append(
                iniciar(
                    comando_ru,
                    arquivos.diretorio / f"ru{indice}.out",
                )
            )

        print(
            f"Aguardando {ESTABILIZACAO_SEGUNDOS}s para estabilização...",
            flush=True,
        )
        time.sleep(ESTABILIZACAO_SEGUNDOS)

        for indice, processo_ru in enumerate(processos_rus, start=1):
            if processo_ru.poll() is not None:
                raise RuntimeError(
                    f"A O-RU {indice} encerrou durante a estabilização "
                    f"(retorno={processo_ru.returncode})."
                )
    except Exception:
        # Importação local evita dependência circular entre os módulos.
        from encerramento import encerrar_processos

        encerrar_processos(
            ProcessosAtivos(processo_gnb, tuple(processos_rus))
        )
        raise

    return ProcessosAtivos(processo_gnb, tuple(processos_rus))
