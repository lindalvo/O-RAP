"""Persistência e retomada das coletas de métricas."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Protocol

DIRETORIO_MAIN = Path(__file__).resolve().parent
DIRETORIO_OUT = (DIRETORIO_MAIN / "../../OUT").resolve()

ARQUIVO_BANCO =DIRETORIO_OUT / "metricas_RMB.db"

SQL_CRIAR_TABELA = """
CREATE TABLE IF NOT EXISTS stats (
    identificador TEXT NOT NULL,
    roundtrip INTEGER NOT NULL,
    cluster_id TEXT NOT NULL,
    cenario TEXT NOT NULL,
    timestamp_utc TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT,
    PRIMARY KEY (
        identificador,
        roundtrip,
        cluster_id,
        cenario,
        timestamp_utc,
        metric
    )
)
"""

SQL_UPSERT = """
INSERT INTO stats (
    identificador,
    roundtrip,
    cluster_id,
    cenario,
    timestamp_utc,
    metric,
    value,
    unit
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (
    identificador,
    roundtrip,
    cluster_id,
    cenario,
    timestamp_utc,
    metric
)
DO UPDATE SET
    value = excluded.value,
    unit = excluded.unit
"""


class TopologiaComChave(Protocol):
    """Atributos necessários para identificar uma topologia no banco."""

    cenario: str

    @property
    def identificador(self) -> str: ...


def conectar(caminho: str | Path = ARQUIVO_BANCO) -> sqlite3.Connection:
    """Abre o banco e garante a existência da tabela de métricas."""
    conexao = sqlite3.connect(Path(caminho), timeout=30)
    conexao.execute(SQL_CRIAR_TABELA)
    return conexao


def chave_topologia(topologia: TopologiaComChave) -> tuple[str, str]:
    """Produz a chave utilizada para controlar a retomada da campanha."""
    return topologia.cenario, topologia.identificador


def upsert_scenario(
    amostras: Iterable[Mapping],
    caminho: str | Path = ARQUIVO_BANCO,
) -> None:
    """Grava todas as amostras recebidas em uma única transação."""
    parametros = (
        (
            amostra["identificador"],
            amostra["roundtrip"],
            amostra["cluster_id"],
            amostra["cenario"],
            amostra["timestamp_utc"],
            amostra["metric"],
            amostra["value"],
            amostra.get("unit"),
        )
        for amostra in amostras
    )

    with conectar(caminho) as conexao:
        conexao.executemany(SQL_UPSERT, parametros)


def carregar_estado_retomada(
    caminho: str | Path = ARQUIVO_BANCO,
) -> tuple[int, set[tuple[str, str]]]:
    """Retorna a maior rodada gravada e suas topologias concluídas."""
    with conectar(caminho) as conexao:
        resultado = conexao.execute(
            "SELECT COALESCE(MAX(roundtrip), 0) FROM stats"
        ).fetchone()
        maior_roundtrip = int(resultado[0])

        if maior_roundtrip == 0:
            return 0, set()

        linhas = conexao.execute(
            """
            SELECT DISTINCT cenario, identificador
            FROM stats
            WHERE roundtrip = ?
            """,
            (maior_roundtrip,),
        )

        concluidas = {(cenario, identificador) for cenario, identificador in linhas}
        return maior_roundtrip, concluidas
