"""Tratamento e correção comuns para DataFrames de métricas.

Função centralizada para:
  - remover leituras inválidas de potência (cpu_package_power > 1000 W),
  - corrigir overflow conhecido da memória para fanout >= 4 (valor < 2000 MB => +4096 MB),
  - arredondar o desvio-padrão dp_carga_mhz para um número configurável de dígitos
    e opcionalmente criar a coluna "dp_key" usada em agregações.

Este módulo permite reaplicar o mesmo pré-processamento em diferentes pontos
do projeto (análises, ILP, geração de gráficos, etc.).
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

DP_ROUND_DIGITS = 6

def clean_metrics_df(df: pd.DataFrame) -> pd.DataFrame:
    """Aplica correções e normalizações conhecidas em um DataFrame de métricas.

    Parâmetros
    - df: DataFrame contendo (pelo menos) as colunas
        ['num_orus', 'metric', 'value', 'dp_carga_mhz'] quando relevantes.

    Retorna uma cópia do DataFrame com as transformações aplicadas.
    """
    if not isinstance(df, pd.DataFrame):
        raise TypeError("df deve ser um pandas.DataFrame")

    df = df.copy()

    # Remove leituras inválidas de potência
    if "metric" in df.columns and "value" in df.columns:
        mask_invalid_power = (
            (df["metric"] == "cpu_package_power") & (df["value"] > 1000.0)
        )
        if mask_invalid_power.any():
            df = df.loc[~mask_invalid_power].copy()

    # Corrige overflow de memória para fanout >= 4 e valores < 2000
    if {"metric", "num_orus", "value"}.issubset(df.columns):
        mask_overflow = (
            (df["metric"] == "memory_usage")
            & (df["num_orus"] >= 4)
            & (df["value"] < 2000.0)
        )
        if mask_overflow.any():
            df.loc[mask_overflow, "value"] = df.loc[mask_overflow, "value"] + 4096.0

    # Arredonda dp_carga_mhz quando presente e opcionalmente cria dp_key
    if "dp_carga_mhz" in df.columns:
        try:
            df["dp_carga_mhz"] = df["dp_carga_mhz"].round(DP_ROUND_DIGITS)
        except Exception:
            # Evita falha caso os valores não sejam numéricos; apenas deixa como está
            pass

    return df


__all__ = ["clean_metrics_df"]
