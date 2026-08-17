"""Comandos para desmontar namespaces e pares veth."""

from __future__ import annotations

from configuracoes import Configuracao
from rede import remover_topologia


def desmontar_topologia(configuracao: Configuracao) -> None:
    """Remove a topologia correspondente ao fanout atual."""
    remover_topologia(
        configuracao.quantidade_rus,
        titulo="Desmontagem da topologia",
    )
