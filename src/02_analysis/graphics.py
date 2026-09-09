#!/usr/bin/env python3
from pathlib import Path
import sqlite3

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.ticker import MultipleLocator
from common.constantes import clean_metrics_df, DIRETORIO_OUT

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


def select_memory_dp_series(per_config: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    FANOUTS: tuple[int, ...] = (2, 3, 4, 5)

    """
    Seleciona uma única série (fanout, carga agregada) para cada fanout.

    Para cada fanout informado, escolhe a carga agregada que possui a maior
    quantidade de valores distintos de DP. O fanout 1 é omitido porque sua
    distribuição contém apenas uma O-RU e, portanto, o desvio padrão não
    fornece variação útil para este gráfico.

    Em caso de empate no número de DPs, prioriza-se a combinação com a maior
    amplitude de DP; persistindo o empate, escolhe-se a menor carga agregada
    para tornar a seleção determinística.
    """
    candidates = per_config.loc[
        per_config["fanout"].isin(FANOUTS)
    ].copy()

    coverage = (
        candidates.groupby(["fanout", "load"], as_index=False)
        .agg(
            n_dps=("std", "nunique"),
            dp_min=("std", "min"),
            dp_max=("std", "max"),
        )
    )

    coverage["dp_span"] = (
        coverage["dp_max"] - coverage["dp_min"]
    )

    # Dentro de cada fanout:
    # 1. maior número de DPs distintos;
    # 2. maior amplitude de DP;
    # 3. menor carga, apenas como desempate determinístico.
    coverage = (
        coverage.sort_values(
            ["fanout", "n_dps", "dp_span", "load"],
            ascending=[True, False, False, True],
        )
        .groupby(
            "fanout",
            as_index=False,
            group_keys=False,
        )
        .head(1)
        .sort_values("fanout")
        .reset_index(drop=True)
    )

    selected = candidates.merge(
        coverage[["fanout", "load"]],
        on=["fanout", "load"],
        how="inner",
    ).sort_values(["fanout", "std"])

    return selected, coverage

def _padded_limits(
    values: pd.Series,
    *,
    pad_fraction: float = 0.05,
    minimum_pad: float = 1.0,
) -> tuple[float, float]:
    """Retorna limites com uma pequena margem visual em torno dos dados."""
    lower = float(values.min())
    upper = float(values.max())
    span = upper - lower
    pad = max(span * pad_fraction, minimum_pad)
    return lower - pad, upper + pad


def draw_memory_dp_chart(
    per_config: pd.DataFrame,
    n_rounds: int,
    output: Path
) -> None:
    """
    Plota memória em função do DP para séries de fanout/carga fixos.

    As séries são as combinações (fanout, carga agregada) de fanouts 4 ou 5
    com maior quantidade de valores distintos de DP. Isso isola visualmente
    a relação entre dispersão interna das cargas e consumo de memória.
    """
    selected, coverage = select_memory_dp_series(
        per_config
    )

    fig, ax = plt.subplots(figsize=FIGSIZE_DP)
    fig.subplots_adjust(left=0.08, right=0.985, bottom=0.12, top=0.76)

    fig.suptitle(
        "Memória por desvio padrão da distribuição das cargas",
        x=0.08,
        y=0.97,
        ha="left",
        fontsize=23,
        fontweight="bold",
    )
    fig.text(
        0.08,
        0.925,
        f"Séries com fanout e carga agregada fixos, consolidadas em {plural_rounds(n_rounds)}; ",
        ha="left",
        fontsize=15,
        color="#4c4c4c",
    )


    for _, row in coverage.iterrows():
        fanout = int(row["fanout"])
        load = float(row["load"])
        n_dps = int(row["n_dps"])

        d = selected.loc[
            (selected["fanout"] == fanout)
            & (selected["load"] == load)
        ].sort_values("std")

        load_label = f"{load:g}"
        ax.plot(
            d["std"],
            d["value"],
            color=COLORS[fanout],
            marker=MARKERS.get(fanout, "o"),
            linewidth=2.2,
            markersize=5.8,
            markeredgecolor="white",
            markeredgewidth=0.5,
            label=f"{fanout} O-RUs, {load_label} MHz ({n_dps} DPs)",
        )

    ax.set_xlabel("Desvio padrão da distribuição das cargas (MHz)")
    ax.set_ylabel("Memória (MB)")

    xlim = _padded_limits(selected["std"], pad_fraction=0.04, minimum_pad=0.75)
    ylim = _padded_limits(selected["value"], pad_fraction=0.06, minimum_pad=25.0)
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.xaxis.set_major_locator(MultipleLocator(5))
    ax.grid(True, alpha=0.22, linewidth=1.0)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    ax.legend(
        title="Fanout e carga agregada",
        loc="upper left",
        bbox_to_anchor=(0.0, 1.22),
        ncol=4,
        frameon=False,
        columnspacing=1.6,
        handlelength=2.2,
        title_fontsize=15,
    )

    fig.text(
        0.07,
        0.028,
        "Valores posteriores ao overflow do contador corrigidos com +4.096 MB.",
        ha="left",
        fontsize=12.5,
        color="#555555",
    )

    # Registra no terminal quais séries foram escolhidas, facilitando a
    # reprodução da figura no texto da dissertação.
    print("\nSéries selecionadas para o gráfico de memória por DP:")
    for row in coverage.itertuples(index=False):
        print(
            f"  fanout={int(row.fanout)}, carga={row.load:g} MHz, "
            f"DPs={int(row.n_dps)}, faixa={row.dp_min:g}..{row.dp_max:g} MHz"
        )

    fig.savefig(output, format="pdf", bbox_inches="tight")
    plt.close(fig)


def generate_all(db_path: Path, output_dir: Path) -> None:
    """Lê ``stats`` e gera os cinco PDFs no diretório informado."""
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
            ""
        ),
    )

    draw_memory_dp_chart(
        memory,
        memory_rounds,
        output_dir / "memoria_por_fanout_carga_dp.pdf"
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
            ""
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
            ""
        ),
    )


if __name__ == "__main__":
    generate_all(DB_PATH, DIRETORIO_OUT)
