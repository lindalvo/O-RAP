"""Compatibilidade para imports históricos.

A implementação real fica em common.comandos.
"""

from common.comandos import executar, iniciar

__all__ = ["executar", "iniciar"]
