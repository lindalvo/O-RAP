"""Persistência transacional das métricas no SQLite."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from configuracoes import Configuracao
from common.constantes import DIRETORIO_OUT


DB_PATH = DIRETORIO_OUT / "metricas.db"


def chave_configuracao(
    configuracao: Configuracao,
) -> tuple[int, int, float]:
    """Chave estável usada pelo banco e pela retomada do pipeline."""
    return (
        configuracao.quantidade_rus,
        configuracao.carga_agregada_mhz,
        round(configuracao.desvio_padrao_mhz, 6),
    )


def _conectar(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conexao = sqlite3.connect(db_path, timeout=30)
    conexao.execute("PRAGMA journal_mode = WAL")
    conexao.execute("PRAGMA busy_timeout = 30000")
    return conexao


def inicializar_banco(db_path: Path = DB_PATH) -> None:
    with _conectar(db_path) as conexao:
        conexao.executescript(
            """
            CREATE TABLE IF NOT EXISTS stats (
                num_orus INTEGER NOT NULL,
                carga_agregada_mhz INTEGER NOT NULL,
                dp_carga_mhz REAL NOT NULL,
                roundtrip INTEGER NOT NULL,
                timestamp_utc TEXT NOT NULL,
                metric TEXT NOT NULL,
                value REAL NOT NULL,
                unit TEXT,

                PRIMARY KEY (
                    num_orus,
                    carga_agregada_mhz,
                    dp_carga_mhz,
                    roundtrip,
                    timestamp_utc,
                    metric
                )
            );

            CREATE INDEX IF NOT EXISTS idx_stats_consulta
            ON stats (
                num_orus,
                carga_agregada_mhz,
                dp_carga_mhz,
                roundtrip,
                metric
            );

            """
        )


def carregar_estado_retomada(
    db_path: Path = DB_PATH,
) -> tuple[int, set[tuple[int, int, float]]]:
    """Retorna a maior rodada e suas configurações presentes em ``stats``."""
    inicializar_banco(db_path)
    with _conectar(db_path) as conexao:
        maior_roundtrip = conexao.execute(
            "SELECT COALESCE(MAX(roundtrip), 0) FROM stats"
        ).fetchone()[0]

        if maior_roundtrip == 0:
            return 0, set()

        linhas = conexao.execute(
            """
            SELECT num_orus, carga_agregada_mhz, dp_carga_mhz
            FROM stats
            WHERE roundtrip = ?
            GROUP BY num_orus, carga_agregada_mhz, dp_carga_mhz
            """
            ,
            (maior_roundtrip,),
        ).fetchall()

    configuracoes_presentes = {
        (int(num_orus), int(carga), round(float(dp), 6))
        for num_orus, carga, dp in linhas
    }
    return int(maior_roundtrip), configuracoes_presentes


def gravar_amostras(
    configuracao: Configuracao,
    roundtrip: int,
    amostras: Iterable[Mapping[str, Any]],
    db_path: Path = DB_PATH,
) -> int:
    """Grava uma coleta completa em uma única transação."""
    num_orus, carga_agregada_mhz, dp_carga_mhz = chave_configuracao(
        configuracao
    )
    valores = [
        (
            num_orus,
            carga_agregada_mhz,
            dp_carga_mhz,
            roundtrip,
            amostra["timestamp_utc"],
            amostra["metric"],
            amostra["value"],
            amostra.get("unit"),
        )
        for amostra in amostras
    ]

    if not valores:
        raise ValueError("Nenhuma amostra recebida para gravação")

    sql = """
        INSERT INTO stats (
            num_orus,
            carga_agregada_mhz,
            dp_carga_mhz,
            roundtrip,
            timestamp_utc,
            metric,
            value,
            unit
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (
            num_orus,
            carga_agregada_mhz,
            dp_carga_mhz,
            roundtrip,
            timestamp_utc,
            metric
        )
        DO UPDATE SET
            value = excluded.value,
            unit = excluded.unit
    """

    conexao = _conectar(db_path)
    try:
        conexao.execute("BEGIN IMMEDIATE")
        conexao.executemany(sql, valores)
        conexao.commit()
    except Exception:
        conexao.rollback()
        raise
    finally:
        conexao.close()

    return len(valores)
