#!/usr/bin/env python3
from pathlib import Path
import sqlite3

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.ticker import MultipleLocator
from common.constantes import clean_metrics_df


DIRETORIO_MAIN = Path(__file__).resolve().parent
DIRETORIO_OUT = (DIRETORIO_MAIN / "../OUT").resolve()
DB_PATH = DIRETORIO_OUT / "metricas.db"


# Valores existentes na coluna stats.metric.
METRIC_CPU = "cpu_usage"
METRIC_MEMORY = "memory_usage"
METRIC_POWER = "cpu_package_power"
METRIC_SCHEDULER = "max_scheduler_latency"


COLORS = {
    1: "#0072B2",
    2: "#56B4E9",
    3: "#009E73",
    4: "#E69F00",
    5: "#D55E00",
}

MARKERS = {
    1: "o",
    2: "s",
    3: "^",
    4: "D",
    5: "P",
}

FANOUT_LABELS = {
    1: "1 O-RU",
    2: "2 O-RUs",
    3: "3 O-RUs",
    4: "4 O-RUs",
    5: "5 O-RUs"
}

FIGSIZE = (16, 9.6)
FIGSIZE_DP = (16, 10.0)

plt.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": 13,
        "axes.labelsize": 16,
        "xtick.labelsize": 13,
        "ytick.labelsize": 13,
        "legend.fontsize": 13,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
    }
)


def load_dataframe(db_path: Path) -> pd.DataFrame:
    """Lê exclusivamente a tabela ``stats`` e aplica a limpeza comum."""
    query = """
        SELECT
            num_orus,
            carga_agregada_mhz,
            dp_carga_mhz,
            roundtrip,
            timestamp_utc,
            metric,
            value,
            unit
        FROM stats
    """

    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql_query(query, conn)

    df = clean_metrics_df(df)
    return df


def prepare_metric(
    df: pd.DataFrame,
    metric_name: str,
) -> tuple[pd.DataFrame, int]:
    """Consolida uma métrica por configuração e entre rodadas.
    Primeiro calcula a mediana das amostras de cada combinação
    ``(num_orus, carga_agregada_mhz, dp_carga_mhz, roundtrip)``. Depois
    calcula a mediana entre as rodadas para obter uma observação por
    configuração ``(num_orus, carga_agregada_mhz, dp_carga_mhz)``.
    """
    d = df.loc[df["metric"] == metric_name].copy()
    if d.empty:
        raise RuntimeError(
            f"A métrica '{metric_name}' não foi encontrada na tabela stats."
        )

    numeric_columns = [
        "num_orus",
        "carga_agregada_mhz",
        "dp_carga_mhz",
        "roundtrip",
        "value",
    ]
    for column in numeric_columns:
        d[column] = pd.to_numeric(d[column], errors="coerce")

    d = d.dropna(subset=numeric_columns).copy()
    d["num_orus"] = d["num_orus"].astype(int)

    n_rounds = int(d["roundtrip"].nunique())

    per_round = (
        d.groupby(
            [
                "num_orus",
                "carga_agregada_mhz",
                "dp_carga_mhz",
                "roundtrip",
            ],
            as_index=False,
        )["value"]
        .median()
    )

    per_config = (
        per_round.groupby(
            ["num_orus", "carga_agregada_mhz", "dp_carga_mhz"],
            as_index=False,
        )["value"]
        .median()
        .rename(
            columns={
                "num_orus": "fanout",
                "carga_agregada_mhz": "load",
                "dp_carga_mhz": "std",
            }
        )
        .sort_values(["fanout", "load", "std"])
    )

    return per_config, n_rounds


def medians_by_fanout_load(per_config: pd.DataFrame) -> pd.DataFrame:
    """Calcula a mediana das configurações com o mesmo fanout e carga."""
    return (
        per_config.groupby(["fanout", "load"], as_index=False)["value"]
        .median()
        .sort_values(["fanout", "load"])
    )


def plural_rounds(n: int) -> str:
    return "1 rodada" if n == 1 else f"{n} rodadas"


def setup_axis(
    ax: plt.Axes,
    *,
    ylabel: str,
    ylim: tuple[float, float],
    ystep: float,
) -> None:
    ax.set_xlabel("Carga agregada (MHz)")
    ax.set_ylabel(ylabel)
    ax.set_xlim(30, 440)
    ax.set_ylim(*ylim)
    ax.xaxis.set_major_locator(MultipleLocator(50))
    ax.yaxis.set_major_locator(MultipleLocator(ystep))
    ax.grid(True, alpha=0.22, linewidth=1.0)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def fanout_handles() -> list[Line2D]:
    return [
        Line2D(
            [0],
            [0],
            color=COLORS[fanout],
            marker=MARKERS[fanout],
            linewidth=2.5,
            markersize=7,
            markeredgecolor="white",
            markeredgewidth=0.6,
            label=FANOUT_LABELS[fanout],
        )
        for fanout in FANOUT_LABELS
    ]


def draw_line_chart(
    per_config: pd.DataFrame,
    n_rounds: int,
    *,
    title: str,
    ylabel: str,
    output: Path,
    ylim: tuple[float, float],
    ystep: float,
    footnote: str,
) -> None:
    data = medians_by_fanout_load(per_config)

    fig, ax = plt.subplots(figsize=FIGSIZE)
    fig.subplots_adjust(left=0.075, right=0.985, bottom=0.12, top=0.84)

    fig.suptitle(
        title,
        x=0.075,
        y=0.965,
        ha="left",
        fontsize=23,
        fontweight="bold",
    )
    fig.text(
        0.075,
        0.915,
        "Mediana das configurações com o mesmo fanout e carga, "
        f"consolidada em {plural_rounds(n_rounds)}",
        ha="left",
        fontsize=15,
        color="#4c4c4c",
    )

    for fanout in FANOUT_LABELS:
        d = data.loc[data["fanout"] == fanout]
        if d.empty:
            continue

        ax.plot(
            d["load"],
            d["value"],
            color=COLORS[fanout],
            marker=MARKERS[fanout],
            linewidth=2.6,
            markersize=6.5,
            markeredgecolor="white",
            markeredgewidth=0.6,
            label=FANOUT_LABELS[fanout],
        )

    setup_axis(ax, ylabel=ylabel, ylim=ylim, ystep=ystep)

    ax.legend(
        title="Fanout",
        loc="upper left",
        bbox_to_anchor=(0.0, 0.985),
        ncol=5,
        frameon=False,
        columnspacing=1.9,
        handlelength=2.2,
        title_fontsize=15,
    )

    fig.text(0.065, 0.028, footnote, ha="left", fontsize=12.5, color="#555555")
    fig.savefig(output, format="pdf", bbox_inches="tight")
    plt.close(fig)


def dp_to_size(dp: np.ndarray | pd.Series | float) -> np.ndarray | float:
    """Converte o DP da composição em área do marcador do scatter."""
    return 26.0 + 3.0 * np.asarray(dp)


def draw_memory_dp_chart(
    per_config: pd.DataFrame,
    n_rounds: int,
    output: Path,
) -> None:
    """Plota memória por fanout/carga, usando o DP no tamanho dos pontos."""
    med = medians_by_fanout_load(per_config)

    fig, ax = plt.subplots(figsize=FIGSIZE_DP)
    fig.subplots_adjust(left=0.07, right=0.985, bottom=0.12, top=0.77)

    fig.suptitle(
        "Memória por fanout e carga agregada",
        x=0.07,
        y=0.97,
        ha="left",
        fontsize=23,
        fontweight="bold",
    )
    fig.text(
        0.07,
        0.925,
        f"Cada ponto representa uma configuração consolidada em {plural_rounds(n_rounds)}; "
        "as linhas mostram a mediana para cada fanout e carga",
        ha="left",
        fontsize=15,
        color="#4c4c4c",
    )

    for fanout in FANOUT_LABELS:
        d = per_config.loc[per_config["fanout"] == fanout]
        if d.empty:
            continue

        ax.scatter(
            d["load"],
            d["value"],
            s=dp_to_size(d["std"]),
            color=COLORS[fanout],
            marker=MARKERS[fanout],
            alpha=0.30,
            linewidths=0,
            zorder=2,
        )

    for fanout in FANOUT_LABELS:
        d = med.loc[med["fanout"] == fanout]
        if d.empty:
            continue

        ax.plot(
            d["load"],
            d["value"],
            color=COLORS[fanout],
            marker=MARKERS[fanout],
            linewidth=2.6,
            markersize=5.5,
            markeredgewidth=0,
            zorder=3,
        )

    setup_axis(
        ax,
        ylabel="Memória (MB)",
        ylim=(2800, 4900),
        ystep=250,
    )

    fanout_legend = fig.legend(
        handles=fanout_handles(),
        title="Fanout",
        loc="upper left",
        bbox_to_anchor=(0.065, 0.885),
        ncol=5,
        frameon=False,
        columnspacing=1.9,
        handlelength=2.2,
        title_fontsize=15,
    )

    dp_refs = [0, 10, 20, 30]
    size_handles = [
        ax.scatter(
            [],
            [],
            s=float(dp_to_size(dp)),
            color="#8f8f8f",
            alpha=0.55,
            edgecolors="#707070",
            linewidths=0.6,
            label=f"{dp} MHz",
        )
        for dp in dp_refs
    ]

    fig.legend(
        handles=size_handles,
        title="DP da composição (tamanho dos pontos)",
        loc="upper right",
        bbox_to_anchor=(0.985, 0.885),
        ncol=4,
        frameon=False,
        columnspacing=1.5,
        handletextpad=0.6,
        title_fontsize=15,
    )
    fig.add_artist(fanout_legend)

    fig.text(
        0.06,
        0.028,
        "Valores posteriores ao overflow do contador corrigidos com +4.096 MB.",
        ha="left",
        fontsize=12.5,
        color="#555555",
    )

    fig.savefig(output, format="pdf", bbox_inches="tight")
    plt.close(fig)


def generate_all(db_path: Path, output_dir: Path) -> None:
    """Lê ``stats`` e gera os cinco PDFs no diretório informado."""
    output_dir.mkdir(parents=True, exist_ok=True)

    df = load_dataframe(db_path)

    cpu, cpu_rounds = prepare_metric(df, METRIC_CPU)
    memory, memory_rounds = prepare_metric(df, METRIC_MEMORY)
    power, power_rounds = prepare_metric(df, METRIC_POWER)
    scheduler, scheduler_rounds = prepare_metric(df, METRIC_SCHEDULER)

    draw_line_chart(
        cpu,
        cpu_rounds,
        title="CPU por fanout e carga agregada",
        ylabel="Uso de CPU (%)",
        output=output_dir / "cpu_por_fanout_carga.pdf",
        ylim=(200, 1400),
        ystep=200,
        footnote=(
            "Valores acima de 100% representam uso agregado de múltiplos núcleos de CPU."
        ),
    )

    draw_line_chart(
        memory,
        memory_rounds,
        title="Memória por fanout e carga agregada",
        ylabel="Memória (MB)",
        output=output_dir / "memoria_por_fanout_carga.pdf",
        ylim=(2800, 4900),
        ystep=250,
        footnote=(
            "Valores posteriores ao overflow do contador corrigidos com +4.096 MB."
        ),
    )

    draw_memory_dp_chart(
        memory,
        memory_rounds,
        output_dir / "memoria_por_fanout_carga_dp.pdf",
    )

    draw_line_chart(
        power,
        power_rounds,
        title="Potência por fanout e carga agregada",
        ylabel="Potência do pacote de CPU (W)",
        output=output_dir / "potencia_por_fanout_carga.pdf",
        ylim=(58, 100),
        ystep=5,
        footnote=(
            "Leituras inválidas de potência foram removidas no tratamento dos dados."
        ),
    )

    draw_line_chart(
        scheduler,
        scheduler_rounds,
        title="Latência máxima do escalonador por fanout e carga agregada",
        ylabel="Latência máxima do escalonador (µs)",
        output=output_dir / "latencia_escalonador_por_fanout_carga.pdf",
        ylim=(40, 135),
        ystep=20,
        footnote=(
            "A métrica representa pressão computacional na O-DU, "
            "não latência de rede ou atraso fim a fim."
        ),
    )


if __name__ == "__main__":
    generate_all(DB_PATH, DIRETORIO_OUT)
