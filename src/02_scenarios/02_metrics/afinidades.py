"""Compatibilidade para imports históricos.

O código real de afinidade fica em common.afinidades.
"""

from common.afinidades import GNB_CPUSETS, GNB_NUMA, RU_CPUSETS, RU_NUMA

__all__ = ["GNB_NUMA", "RU_NUMA", "GNB_CPUSETS", "RU_CPUSETS"]
