import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from common.afinidades import GNB_CPUSETS, GNB_NUMA, RU_CPUSETS, RU_NUMA
from common.comandos import iniciar
from common.constantes import HOST_METRICAS, PORTA_METRICAS, TIMEOUT_METRICAS_SEGUNDOS, INTERVALO_TENTATIVAS_SEGUNDOS, ESTABILIZACAO_SEGUNDOS
from configuracoes import Configuracao
from yamls import ArquivosConfiguracao

class ErroProcesso(RuntimeError):
    """Falha recuperável durante a inicialização dos processos."""

@dataclass(frozen=True)
class ProcessosAtivos:
    gnb: subprocess.Popen
    rus: tuple[subprocess.Popen, ...]


def aguardar_porta_metricas(
    processo_gnb: subprocess.Popen | None = None,
) -> None:
    """Aguarda uma conexão TCP com o serviço de métricas da gNB."""
    limite = time.monotonic() + TIMEOUT_METRICAS_SEGUNDOS

    while time.monotonic() < limite:
        if processo_gnb is not None and processo_gnb.poll() is not None:
            raise ErroProcesso(
                "A gNB encerrou antes de disponibilizar a porta de métricas "
                f"(retorno={processo_gnb.returncode})."
            )
        try:
            with socket.create_connection((HOST_METRICAS, PORTA_METRICAS), timeout=1):
                print(f"Serviço de métricas disponível em {HOST_METRICAS}:{PORTA_METRICAS}.")
                return
        except OSError:
            restante = limite - time.monotonic()
            if restante > 0:
                time.sleep(min(INTERVALO_TENTATIVAS_SEGUNDOS, restante))

    raise ErroProcesso(
        f"Serviço de métricas indisponível em {HOST_METRICAS}:{PORTA_METRICAS} "
        f"após {TIMEOUT_METRICAS_SEGUNDOS:g} segundos."
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
    try:
        processo_gnb = iniciar(comando_gnb, arquivos.diretorio / "gnb.out")
    except (OSError, subprocess.SubprocessError) as exc:
        raise ErroProcesso(f"Falha ao iniciar a gNB: {exc}") from exc

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
        if processo_gnb.poll() is not None:
            raise ErroProcesso(f"A gNB encerrou durante a estabilização (retorno={processo_gnb.returncode}).")
        for indice, processo_ru in enumerate(processos_rus, start=1):
            if processo_ru.poll() is not None:
                raise ErroProcesso(
                    f"A O-RU {indice} encerrou durante a estabilização "
                    f"(retorno={processo_ru.returncode})."
                )
    except ErroProcesso:
        # Importação local evita dependência circular entre os módulos.
        from encerramento import encerrar_processos
        encerrar_processos(ProcessosAtivos(processo_gnb, tuple(processos_rus)))
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        from encerramento import encerrar_processos
        encerrar_processos(ProcessosAtivos(processo_gnb, tuple(processos_rus)))
        raise ErroProcesso(f"Falha ao iniciar uma O-RU: {exc}") from exc
    return ProcessosAtivos(processo_gnb, tuple(processos_rus))
