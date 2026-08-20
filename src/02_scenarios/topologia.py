"""Montagem da rede O-DU/O-RU para uma topologia experimental."""

from __future__ import annotations

import subprocess

from comandos import executar
from configuracoes import Topologia
from erros import ErroTopologia
from rede import remover_topologia


MTU = 9000
TAMANHO_FILA = 10000
ATRASO_FIXO_US = 60
LIMITE_NETEM = 100000


def _comandos_da_ru(indice_ru: int, atraso_us: int) -> list[list[str]]:
    """Produz os comandos de rede de uma O-RU."""
    namespace = f"ru{indice_ru}"
    interface_du = f"ofh_du{indice_ru}"
    interface_ru = f"ofh_ru{indice_ru}"

    # Endereços definidos nas matrizes YAML da gNB e das O-RUs.
    mac_du = f"02:0d:3f:d0:00:{indice_ru:02x}"
    mac_ru = f"02:0d:3f:e0:00:{indice_ru:02x}"

    return [
        ["ip", "netns", "add", namespace],
        [
            "ip",
            "link",
            "add",
            interface_du,
            "type",
            "veth",
            "peer",
            "name",
            interface_ru,
        ],
        ["ip", "link", "set", interface_ru, "netns", namespace],
        ["ip", "link", "set", "dev", interface_du, "address", mac_du],
        [
            "ip",
            "netns",
            "exec",
            namespace,
            "ip",
            "link",
            "set",
            "dev",
            interface_ru,
            "address",
            mac_ru,
        ],
        ["ip", "link", "set", "dev", interface_du, "mtu", str(MTU)],
        [
            "ip",
            "netns",
            "exec",
            namespace,
            "ip",
            "link",
            "set",
            "dev",
            interface_ru,
            "mtu",
            str(MTU),
        ],
        [
            "ip",
            "link",
            "set",
            "dev",
            interface_du,
            "txqueuelen",
            str(TAMANHO_FILA),
        ],
        [
            "ip",
            "netns",
            "exec",
            namespace,
            "ip",
            "link",
            "set",
            "dev",
            interface_ru,
            "txqueuelen",
            str(TAMANHO_FILA),
        ],
        ["sysctl", "-w", f"net.ipv6.conf.{interface_du}.disable_ipv6=1"],
        [
            "ip",
            "netns",
            "exec",
            namespace,
            "sysctl",
            "-w",
            f"net.ipv6.conf.{interface_ru}.disable_ipv6=1",
        ],
        [
            "ethtool",
            "-K",
            interface_du,
            "gro",
            "off",
            "gso",
            "off",
            "tso",
            "off",
            "rx",
            "off",
            "tx",
            "off",
        ],
        [
            "ip",
            "netns",
            "exec",
            namespace,
            "ethtool",
            "-K",
            interface_ru,
            "gro",
            "off",
            "gso",
            "off",
            "tso",
            "off",
            "rx",
            "off",
            "tx",
            "off",
        ],
        ["ip", "link", "set", "dev", interface_du, "up"],
        [
            "ip",
            "netns",
            "exec",
            namespace,
            "ip",
            "link",
            "set",
            "dev",
            "lo",
            "up",
        ],
        [
            "ip",
            "netns",
            "exec",
            namespace,
            "ip",
            "link",
            "set",
            "dev",
            interface_ru,
            "up",
        ],
        # Egress da O-DU: tráfego O-DU -> O-RU.
        [
            "tc",
            "qdisc",
            "replace",
            "dev",
            interface_du,
            "root",
            "netem",
            "delay",
            f"{atraso_us}us",
            "limit",
            str(LIMITE_NETEM),
        ],
        # Egress da O-RU: tráfego O-RU -> O-DU.
        [
            "ip",
            "netns",
            "exec",
            namespace,
            "tc",
            "qdisc",
            "replace",
            "dev",
            interface_ru,
            "root",
            "netem",
            "delay",
            f"{atraso_us}us",
            "limit",
            str(LIMITE_NETEM),
        ],
    ]


def montar_topologia(topologia: Topologia) -> None:
    """Monta um namespace e um par veth para cada O-RU da topologia."""
    remover_topologia(
        topologia.quantidade_rus,
        titulo="Preparação da topologia",
    )
    print(
        f"\n=== Montagem: {topologia.cenario}/"
        f"{topologia.identificador} ===",
        flush=True,
    )

    try:
        for indice_ru, ru in enumerate(topologia.rus, start=1):
            atraso_total_us = ATRASO_FIXO_US + ru.delay_us
            print(
                f"O-RU {indice_ru}: numestacao={ru.numestacao} "
                f"delay={ATRASO_FIXO_US}+{ru.delay_us}="
                f"{atraso_total_us}us por sentido",
                flush=True,
            )

            for comando in _comandos_da_ru(indice_ru, atraso_total_us):
                executar(comando)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ErroTopologia(
            f"Falha ao montar {topologia.cenario}/"
            f"{topologia.identificador}: {exc}"
        ) from exc
