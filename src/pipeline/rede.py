"""Remoção idempotente dos recursos de rede do pipeline."""

from __future__ import annotations

import time
from pathlib import Path

from comandos import executar


TENTATIVAS_REMOCAO_NAMESPACE = 3
INTERVALO_REMOCAO_SEGUNDOS = 0.2


def _executar_tolerando_ausencia(comando: list[str]) -> None:
    executar(comando, verificar=False, ocultar_stderr=True)


def remover_topologia(quantidade_rus: int, titulo: str) -> None:
    """Remove qdiscs, namespaces e veths, inclusive resíduos anteriores."""
    print(f"\n=== {titulo} ===", flush=True)

    for indice_ru in reversed(range(1, quantidade_rus + 1)):
        namespace = f"ru{indice_ru}"
        interface_du = f"ofh_du{indice_ru}"
        caminho_namespace = Path("/run/netns") / namespace

        _executar_tolerando_ausencia(
            ["tc", "qdisc", "del", "dev", interface_du, "root"]
        )

        namespace_removido = False
        for tentativa in range(TENTATIVAS_REMOCAO_NAMESPACE):
            _executar_tolerando_ausencia(
                ["ip", "netns", "del", namespace]
            )
            if not caminho_namespace.exists():
                namespace_removido = True
                break
            if tentativa + 1 < TENTATIVAS_REMOCAO_NAMESPACE:
                time.sleep(INTERVALO_REMOCAO_SEGUNDOS)

        if not namespace_removido:
            raise RuntimeError(
                f"O namespace {namespace!r} permaneceu em "
                f"{caminho_namespace} após "
                f"{TENTATIVAS_REMOCAO_NAMESPACE} tentativas de remoção."
            )

        # Normalmente o par veth desaparece com o namespace. Este comando
        # cobre uma interface da O-DU que tenha permanecido no namespace raiz.
        _executar_tolerando_ausencia(
            ["ip", "link", "del", interface_du]
        )
