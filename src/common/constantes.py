"""Funcoes globais compartilhadas por todos os pipelines."""
from math import radians, sin, cos, atan2, sqrt
from datetime import datetime, timezone
from pathlib import Path
import pickle
from typing import Optional
import pandas as pd


MAXIMO_AGREGADO_MHZ = int(430)
MAXIMO_RUS = int(4)
DIRETORIO_MAIN = Path(__file__).resolve().parent
DIRETORIO_OUT = (DIRETORIO_MAIN / "../OUT").resolve()
DIRETORIO_ASSETS = (DIRETORIO_MAIN / "../assets").resolve()
EARTH_RADIUS_KM: float = 6371.0
MAX_FIBER_DISTANCE_KM = float(9)
DP_ROUND_DIGITS = int(6)
DURACAO_COLETA_SEGUNDOS = 30
TIMEOUT_CONEXAO_SEGUNDOS = 10
HOST_METRICAS = "127.0.0.1"
PORTA_METRICAS = 8001
URL_METRICAS = f"ws://{HOST_METRICAS}:{PORTA_METRICAS}"
TIMEOUT_METRICAS_SEGUNDOS = int(60)
INTERVALO_TENTATIVAS_SEGUNDOS = int(1)
ESTABILIZACAO_SEGUNDOS = int(7)
RODADAS = int(10)
MAX_TENTATIVAS = int(5)
ATRASO_DU_RU_US = int(60)

def designacao_para_mhz(designacao: str) -> int:
    bw = designacao[:4].upper()

    if "M" in bw:
        partes = bw.split("M")
        mhz = float(f"{partes[0]}.{partes[1] or '0'}")
    elif "K" in bw:
        partes = bw.split("K")
        khz = float(f"{partes[0]}.{partes[1] or '0'}")
        mhz = khz / 1000
    elif "G" in bw:
        partes = bw.split("G")
        ghz = float(f"{partes[0]}.{partes[1] or '0'}")
        mhz = ghz * 1000
    else:
        mhz = float(bw)

    return int(mhz)

def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    a = sin(dphi/2)**2 + cos(phi1)*cos(phi2)*sin(dlambda/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    return EARTH_RADIUS_KM * c

def normalize_timestamp_utc(timestamp):
    if isinstance(timestamp, datetime):
        dt = timestamp
    else:
        timestamp = str(timestamp)

        if timestamp.endswith("Z"):
            timestamp = timestamp[:-1] + "+00:00"

        dt = datetime.fromisoformat(timestamp)

    if dt.tzinfo is None:
        # Somente está correto se o timestamp sem timezone já representar UTC.
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    return dt.isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")

def larguras_mhz() -> tuple:
    with open(DIRETORIO_OUT / 'bandwidths.pkl', "rb") as arquivo:
        resultado = pickle.load(arquivo)
    return resultado

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

    if "dp_carga_mhz" in df.columns:
        try:
            df["dp_carga_mhz"] = df["dp_carga_mhz"].round(DP_ROUND_DIGITS)
        except Exception:
            # Evita falha caso os valores não sejam numéricos; apenas deixa como está
            pass

    return df

