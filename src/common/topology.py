"""Helpers de topologia reutilizáveis pelos pipelines.

Fornece construtor de comandos para montar pares veth/namespace por O-RU
comportando atraso configurável por sentido e opção para aplicar o netem
apenas no egress da O-DU (comportamento do pipeline 01_metrics).
"""

from __future__ import annotations

MTU = 9000
TAMANHO_FILA = 10000
LIMITE_NETEM = 100000


def comandos_da_ru(indice_ru: int, atraso_us: int, ambos_sentidos: bool = True) -> list[list[str]]:
    """Gera a lista de comandos de montagem da O-RU."""
    namespace = f"ru{indice_ru}"
    interface_du = f"ofh_du{indice_ru}"
    interface_ru = f"ofh_ru{indice_ru}"

    mac_du = f"02:0d:3f:d0:00:{indice_ru:02x}"
    mac_ru = f"02:0d:3f:e0:00:{indice_ru:02x}"

    comandos = [
        ["ip", "netns", "add", namespace],
        ["ip", "link", "add", interface_du, "type", "veth", "peer", "name", interface_ru],
        ["ip", "link", "set", interface_ru, "netns", namespace],
        ["ip", "link", "set", "dev", interface_du, "address", mac_du],
        ["ip", "netns", "exec", namespace, "ip", "link", "set", "dev", interface_ru, "address", mac_ru],
        ["ip", "link", "set", "dev", interface_du, "mtu", str(MTU)],
        ["ip", "netns", "exec", namespace, "ip", "link", "set", "dev", interface_ru, "mtu", str(MTU)],
        ["ip", "link", "set", "dev", interface_du, "txqueuelen", str(TAMANHO_FILA)],
        ["ip", "netns", "exec", namespace, "ip", "link", "set", "dev", interface_ru, "txqueuelen", str(TAMANHO_FILA)],
        ["sysctl", "-w", f"net.ipv6.conf.{interface_du}.disable_ipv6=1"],
        ["ip", "netns", "exec", namespace, "sysctl", "-w", f"net.ipv6.conf.{interface_ru}.disable_ipv6=1"],
        ["ethtool", "-K", interface_du, "gro", "off", "gso", "off", "tso", "off", "rx", "off", "tx", "off"],
        ["ip", "netns", "exec", namespace, "ethtool", "-K", interface_ru, "gro", "off", "gso", "off", "tso", "off", "rx", "off", "tx", "off"],
        ["ip", "link", "set", "dev", interface_du, "up"],
        ["ip", "netns", "exec", namespace, "ip", "link", "set", "dev", "lo", "up"],
        ["ip", "netns", "exec", namespace, "ip", "link", "set", "dev", interface_ru, "up"],
    ]

    comandos.append([
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
    ])

    if ambos_sentidos:
        comandos.append([
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
        ])

    return comandos


__all__ = ["MTU", "TAMANHO_FILA", "LIMITE_NETEM", "comandos_da_ru"]
