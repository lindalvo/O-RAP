"""Comandos para desmontar namespaces e pares veth."""

from __future__ import annotations

from shlex import join

from configuracoes import Configuracao


def _imprimir_tolerando_ausencia(comando: list[str]) -> None:
    print(f"+ {join(comando)} 2>/dev/null || true")


def desmontar_topologia(configuracao: Configuracao) -> None:
    """Imprime a remoção da topologia correspondente ao fanout atual."""
    print("\n=== Desmontagem da topologia ===")

    for indice_ru in reversed(
        range(1, configuracao.quantidade_rus + 1)
    ):
        namespace = f"ru{indice_ru}"
        interface_du = f"ofh_du{indice_ru}"

        _imprimir_tolerando_ausencia(
            ["tc", "qdisc", "del", "dev", interface_du, "root"]
        )
        _imprimir_tolerando_ausencia(
            ["ip", "netns", "del", namespace]
        )
        # A exclusão do namespace normalmente remove o par veth. Este comando
        # cobre o caso em que a interface do lado da O-DU tenha permanecido.
        _imprimir_tolerando_ausencia(
            ["ip", "link", "del", interface_du]
        )
