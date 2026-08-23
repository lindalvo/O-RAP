"""Compatibilidade para imports históricos.

A implementação real de remoção de topologia fica em common.rede.
"""

from common.rede import remover_topologia

__all__ = ["remover_topologia"]
