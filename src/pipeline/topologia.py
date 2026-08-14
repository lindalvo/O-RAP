"""Geração dos comandos de montagem da topologia O-DU/O-RU."""

from __future__ import annotations

from comandos import executar
from configuracoes import Configuracao


MTU = 9000
TAMANHO_FILA = 10000
ATRASO_DU_RU_US = 60
LIMITE_NETEM = 100000


def montar_topologia(configuracao: Configuracao) -> None:
    """Monta uma topologia com um namespace e uma veth por O-RU."""
    print(f"\n=== Montagem: {configuracao.identificador} ===")

    for indice_ru in range(1, configuracao.quantidade_rus + 1):
        namespace = f"ru{indice_ru}"
        interface_du = f"ofh_du{indice_ru}"
        interface_ru = f"ofh_ru{indice_ru}"

        # Endereços definidos nas matrizes YAML da gNB e das RUs.
        mac_du = f"02:0d:3f:d0:00:{indice_ru:02x}"
        mac_ru = f"02:0d:3f:e0:00:{indice_ru:02x}"

        comandos = [
            ["ip", "netns", "add", namespace],
            [
                "ip", "link", "add", interface_du,
                "type", "veth", "peer", "name", interface_ru,
            ],
            ["ip", "link", "set", interface_ru, "netns", namespace],
            ["ip", "link", "set", "dev", interface_du, "address", mac_du],
            [
                "ip", "netns", "exec", namespace,
                "ip", "link", "set", "dev", interface_ru,
                "address", mac_ru,
            ],
            ["ip", "link", "set", "dev", interface_du, "mtu", str(MTU)],
            [
                "ip", "netns", "exec", namespace,
                "ip", "link", "set", "dev", interface_ru,
                "mtu", str(MTU),
            ],
            [
                "ip", "link", "set", "dev", interface_du,
                "txqueuelen", str(TAMANHO_FILA),
            ],
            [
                "ip", "netns", "exec", namespace,
                "ip", "link", "set", "dev", interface_ru,
                "txqueuelen", str(TAMANHO_FILA),
            ],
            [
                "sysctl", "-w",
                f"net.ipv6.conf.{interface_du}.disable_ipv6=1",
            ],
            [
                "ip", "netns", "exec", namespace,
                "sysctl", "-w",
                f"net.ipv6.conf.{interface_ru}.disable_ipv6=1",
            ],
            [
                "ethtool", "-K", interface_du,
                "gro", "off", "gso", "off", "tso", "off",
                "rx", "off", "tx", "off",
            ],
            [
                "ip", "netns", "exec", namespace,
                "ethtool", "-K", interface_ru,
                "gro", "off", "gso", "off", "tso", "off",
                "rx", "off", "tx", "off",
            ],
            ["ip", "link", "set", "dev", interface_du, "up"],
            [
                "ip", "netns", "exec", namespace,
                "ip", "link", "set", "dev", "lo", "up",
            ],
            [
                "ip", "netns", "exec", namespace,
                "ip", "link", "set", "dev", interface_ru, "up",
            ],
            # O qdisc fica na interface da O-DU: atraso apenas O-DU -> O-RU.
            [
                "tc", "qdisc", "replace", "dev", interface_du,
                "root", "netem", "delay", f"{ATRASO_DU_RU_US}us",
                "limit", str(LIMITE_NETEM),
            ],
        ]

        for comando in comandos:
            executar(comando)
