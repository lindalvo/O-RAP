import sqlite3
from pathlib import Path
from typing import Dict

import numpy as np
import pandas as pd
from common.constantes import (
    DIRETORIO_OUT,
    DP_ROUND_DIGITS,
    clean_metrics_df,
)

# Base de caracterização: custos empíricos usados pelos ILPs.
CHARACTERIZATION_DB_PATH = DIRETORIO_OUT / "metricas.db"

# Base obtida na execução das associações no ICARUS.
EXECUTION_DB_PATH = DIRETORIO_OUT / "metricas_RMB.db"

SCENARIOS = (
    "minlink",
    "mincpu",
    "minmemory",
    "minpower",
    "minmaxsched",
)

COMPARISON_SCENARIOS = (
    "mincpu",
    "minmemory",
    "minpower",
    "minmaxsched",
)

METRIC_SPECS = {
    "CPU": {
        "metric": "cpu_usage",
        "usar_dp": False,
        "global_aggregation": "sum",
    },
    "Memoria": {
        "metric": "memory_usage",
        "usar_dp": True,
        "global_aggregation": "sum",
    },
    "Potencia": {
        "metric": "cpu_package_power",
        "usar_dp": False,
        "global_aggregation": "sum",
    },
    "MaxSchedulerLatency": {
        "metric": "max_scheduler_latency",
        "usar_dp": False,
        "global_aggregation": "max",
    },
}

GLOBAL_COLUMN_NAMES = {
    "CPU": "CPU",
    "Memoria": "Memoria",
    "Potencia": "Potencia",
    "MaxSchedulerLatency": "MaiorLatencia",
}

STRUCTURAL_FIELDS = [
    "NumRUs",
    "TotalLinkDistanceKM",
    "AggregatedLoadMHz",
    "DPLoadMHz",
]

METRIC_FIELDS = list(METRIC_SPECS)


def add_link_distance(df: pd.DataFrame, df_dm: pd.DataFrame) -> pd.DataFrame:
    """Adiciona a distância RU -> DU de cada associação."""
    result = df.copy()
    result["NumEstacao"] = result["NumEstacao"].astype(int)
    result["O-DU"] = result["O-DU"].astype(int)
    result["bandwidth"] = pd.to_numeric(
        result["bandwidth"], errors="raise"
    ).astype(float)
    result["LinkDistanceKM"] = [
        float(df_dm.at[ru_id, du_id])
        for ru_id, du_id in zip(
            result["NumEstacao"],
            result["O-DU"],
        )
    ]
    return result


def calcular_assinaturas_odus(df: pd.DataFrame) -> pd.DataFrame:
    """Calcula fanout, carga agregada e DP populacional das cargas por O-DU."""
    rows = []
    for du, group in df.groupby("O-DU", sort=True):
        loads = group["bandwidth"].astype(float).to_numpy()
        num_orus = int(len(loads))
        total_load = int(round(float(loads.sum())))
        dp_load = round(float(np.std(loads, ddof=0)), DP_ROUND_DIGITS)
        rows.append(
            {
                "O-DU": int(du),
                "num_orus": num_orus,
                "carga_agregada_mhz": total_load,
                "dp_carga_mhz": dp_load,
            }
        )
    return pd.DataFrame(rows)


def build_structure_table(df: pd.DataFrame) -> pd.DataFrame:
    """Gera uma linha por O-DU com a estrutura da associação."""
    signatures = calcular_assinaturas_odus(df).set_index("O-DU")

    result = (
        df.groupby("O-DU", sort=True)
        .agg(
            **{
                "O-DU_ID": ("O-DU_ID", "first"),
                "NumRUs": ("NumEstacao", "count"),
                "TotalLinkDistanceKM": ("LinkDistanceKM", "sum"),
                "AggregatedLoadMHz": ("bandwidth", "sum"),
            }
        )
    )
    result["O-DU_ID"] = result["O-DU_ID"].astype("Int64")
    result["NumRUs"] = result["NumRUs"].astype(int)
    result["TotalLinkDistanceKM"] = result["TotalLinkDistanceKM"].astype(float)
    result["AggregatedLoadMHz"] = result["AggregatedLoadMHz"].astype(float)
    result["DPLoadMHz"] = signatures["dp_carga_mhz"].astype(float)
    result.index = result.index.astype(int)
    result.index.name = "O-DU"
    return result


def load_scenario_associations(
    ta: pd.DataFrame,
    df_dm: pd.DataFrame,
) -> tuple[Dict[str, pd.DataFrame], Dict[str, pd.DataFrame]]:
    """Carrega os cinco cenários e constrói suas tabelas estruturais."""
    associations: Dict[str, pd.DataFrame] = {}
    structures: Dict[str, pd.DataFrame] = {}

    for scenario in SCENARIOS:
        path = DIRETORIO_OUT / f"ilp_RMB_{scenario}.csv"
        print(f"Carregando o arquivo {path}")
        clusters = pd.read_csv(path)
        clusters["O-DU"] = clusters["O-DU"].astype("int64")
        clusters = clusters.merge(
            ta[["O-DU", "O-DU_ID"]],
            on="O-DU",
            how="left",
            validate="m:1",
        )
        clusters = add_link_distance(clusters, df_dm)
        associations[scenario] = clusters
        structures[scenario] = build_structure_table(clusters)

    return associations, structures


# ---------------------------------------------------------------------------
# Custos previstos a partir da base de caracterização metricas.db
# ---------------------------------------------------------------------------

def load_characterization_dataframe(db_path: Path) -> pd.DataFrame:
    """Lê metricas.db e aplica as correções comuns da base empírica."""
    query = """
        SELECT
            num_orus,
            carga_agregada_mhz,
            dp_carga_mhz,
            roundtrip,
            metric,
            value
        FROM stats
    """
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
        df = pd.read_sql_query(query, connection)
    return clean_metrics_df(df)


def consolidar_custos_metricos(
    df: pd.DataFrame,
    metric_name: str,
    usar_dp: bool,
) -> pd.DataFrame:
    """Consolida os custos na mesma granularidade usada pelos ILPs."""
    metric_df = df.loc[df["metric"].eq(metric_name)].copy()
    full_keys = ["num_orus", "carga_agregada_mhz", "dp_carga_mhz"]

    per_configuration_round = (
        metric_df.groupby(full_keys + ["roundtrip", "metric"], as_index=False)[
            "value"
        ]
        .mean()
    )

    if usar_dp:
        keys = full_keys
        per_round = per_configuration_round
    else:
        keys = ["num_orus", "carga_agregada_mhz"]
        per_round = (
            per_configuration_round.groupby(
                keys + ["roundtrip", "metric"], as_index=False
            )["value"]
            .mean()
        )

    consolidated = (
        per_round.groupby(keys + ["metric"], as_index=False)["value"]
        .mean()
        .rename(columns={"value": "custo_empirico"})
    )
    consolidated["num_orus"] = consolidated["num_orus"].astype(int)
    consolidated["carga_agregada_mhz"] = consolidated[
        "carga_agregada_mhz"
    ].astype(int)
    return consolidated


def carregar_custos_por_metrica(df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """Constrói os índices empíricos das quatro métricas."""
    return {
        output_name: consolidar_custos_metricos(
            df,
            spec["metric"],
            spec["usar_dp"],
        )
        for output_name, spec in METRIC_SPECS.items()
    }


def estimar_metrica_por_odu(
    signatures: pd.DataFrame,
    custos: pd.DataFrame,
    usar_dp: bool,
) -> Dict[int, float]:
    """Busca o custo empírico da assinatura de cada O-DU."""
    if usar_dp:
        cost_index = {
            (
                int(row.num_orus),
                int(row.carga_agregada_mhz),
                round(float(row.dp_carga_mhz), DP_ROUND_DIGITS),
            ): float(row.custo_empirico)
            for row in custos.itertuples(index=False)
        }
    else:
        cost_index = {
            (int(row.num_orus), int(row.carga_agregada_mhz)): float(
                row.custo_empirico
            )
            for row in custos.itertuples(index=False)
        }

    result: Dict[int, float] = {}
    for _, row in signatures.iterrows():
        if usar_dp:
            key = (
                int(row["num_orus"]),
                int(row["carga_agregada_mhz"]),
                round(float(row["dp_carga_mhz"]), DP_ROUND_DIGITS),
            )
        else:
            key = (
                int(row["num_orus"]),
                int(row["carga_agregada_mhz"]),
            )
        result[int(row["O-DU"])] = cost_index[key]
    return result


def build_predicted_odu_table(
    association: pd.DataFrame,
    structure: pd.DataFrame,
    custos_por_metrica: Dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """Acrescenta à estrutura os quatro custos previstos por O-DU."""
    result = structure.copy()
    signatures = calcular_assinaturas_odus(association)

    for output_name, spec in METRIC_SPECS.items():
        estimates = estimar_metrica_por_odu(
            signatures,
            custos_por_metrica[output_name],
            spec["usar_dp"],
        )
        result[output_name] = pd.Series(estimates, dtype=float)

    return result


# ---------------------------------------------------------------------------
# Métricas aferidas na execução das associações no ICARUS
# ---------------------------------------------------------------------------

def build_execution_metadata(
    structures: Dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """Converte a estrutura dos cenários em metadados para limpar metricas_RMB."""
    frames = []
    for scenario, table in structures.items():
        frame = table.reset_index().rename(
            columns={
                "O-DU": "cluster_id",
                "NumRUs": "num_orus",
                "AggregatedLoadMHz": "carga_agregada_mhz",
                "DPLoadMHz": "dp_carga_mhz",
            }
        )
        frame["cenario"] = scenario
        frames.append(
            frame[
                [
                    "cenario",
                    "cluster_id",
                    "num_orus",
                    "carga_agregada_mhz",
                    "dp_carga_mhz",
                    "O-DU_ID",
                ]
            ]
        )
    return pd.concat(frames, ignore_index=True)


def load_execution_dataframe(
    db_path: Path,
    structures: Dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """
    Lê metricas_RMB.db, anexa fanout/carga/DP da associação correspondente
    e então aplica clean_metrics_df. Isso mantém a correção de overflow de
    memória dependente do fanout também na base de execução.
    """
    query = """
        SELECT
            identificador,
            roundtrip,
            cluster_id,
            cenario,
            timestamp_utc,
            metric,
            value,
            unit
        FROM stats
    """
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
        df = pd.read_sql_query(query, connection)

    df["cenario"] = (
        df["cenario"].astype(str).str.strip().str.replace(r"^ilp_", "", regex=True)
    )
    df["cluster_id"] = pd.to_numeric(df["cluster_id"], errors="raise").astype(int)

    metadata = build_execution_metadata(structures)
    df = df.merge(
        metadata,
        on=["cenario", "cluster_id"],
        how="left",
        validate="m:1",
    )
    df = clean_metrics_df(df)
    return df


def consolidar_metricas_aferidas_por_odu(df: pd.DataFrame) -> pd.DataFrame:
    """
    Consolida a execução com a mesma lógica temporal usada na caracterização:
    média das amostras em cada roundtrip e, depois, média entre roundtrips.
    """
    metric_names = [spec["metric"] for spec in METRIC_SPECS.values()]
    selected = df.loc[df["metric"].isin(metric_names)].copy()

    per_round = (
        selected.groupby(
            ["cenario", "cluster_id", "roundtrip", "metric"],
            as_index=False,
        )["value"]
        .mean()
    )
    consolidated = (
        per_round.groupby(
            ["cenario", "cluster_id", "metric"],
            as_index=False,
        )["value"]
        .mean()
        .pivot(
            index=["cenario", "cluster_id"],
            columns="metric",
            values="value",
        )
        .reset_index()
    )

    rename = {
        spec["metric"]: output_name
        for output_name, spec in METRIC_SPECS.items()
    }
    return consolidated.rename(columns=rename)


def build_measured_odu_tables(
    measured: pd.DataFrame,
    structures: Dict[str, pd.DataFrame],
) -> Dict[str, pd.DataFrame]:
    """Combina métricas aferidas e estrutura de cada cenário."""
    result: Dict[str, pd.DataFrame] = {}

    for scenario in SCENARIOS:
        table = structures[scenario].copy()
        scenario_metrics = measured.loc[
            measured["cenario"].eq(scenario)
        ].copy()
        scenario_metrics["cluster_id"] = scenario_metrics["cluster_id"].astype(int)
        scenario_metrics = scenario_metrics.set_index("cluster_id")

        for metric in METRIC_FIELDS:
            table[metric] = scenario_metrics[metric]

        result[scenario] = table

    return result


def agregar_metricas_globais(
    table: pd.DataFrame,
    scenario: str,
) -> Dict[str, float | str]:
    """Soma CPU/memória/potência e usa o máximo das latências por O-DU."""
    result: Dict[str, float | str] = {"Scenario": scenario}

    for metric, spec in METRIC_SPECS.items():
        output_column = GLOBAL_COLUMN_NAMES[metric]
        if spec["global_aggregation"] == "sum":
            result[output_column] = float(table[metric].sum(min_count=1))
        else:
            result[output_column] = float(table[metric].max())

    return result


# ---------------------------------------------------------------------------
# Comparações e saídas
# ---------------------------------------------------------------------------

def build_minlink_scenario_comparison(
    reference: pd.DataFrame,
    scenario: pd.DataFrame,
    scenario_name: str,
) -> pd.DataFrame:
    """Compara, por O-DU, minlink e um cenário usando métricas aferidas."""
    index = reference.index.union(scenario.index).sort_values()
    ref = reference.reindex(index)
    cur = scenario.reindex(index)

    result = pd.DataFrame(index=index)
    result.index.name = "O-DU"
    result["O-DU_ID"] = ref["O-DU_ID"].combine_first(cur["O-DU_ID"])

    for field in STRUCTURAL_FIELDS + METRIC_FIELDS:
        result[f"minlink_{field}"] = ref[field]
        result[f"{scenario_name}_{field}"] = cur[field]

    return result


def build_previsto_aferido_comparison(
    previsto: pd.DataFrame,
    aferido: pd.DataFrame,
) -> pd.DataFrame:
    """Compara custos previstos e aferidos por O-DU no mesmo cenário."""
    index = previsto.index.union(aferido.index).sort_values()
    p = previsto.reindex(index)
    a = aferido.reindex(index)

    result = pd.DataFrame(index=index)
    result.index.name = "O-DU"
    result["O-DU_ID"] = p["O-DU_ID"].combine_first(a["O-DU_ID"])

    for field in STRUCTURAL_FIELDS:
        result[field] = p[field].combine_first(a[field])

    for metric in METRIC_FIELDS:
        predicted = p[metric].astype(float)
        measured = a[metric].astype(float)
        difference = measured - predicted
        pct = np.where(
            predicted.ne(0) & predicted.notna() & measured.notna(),
            difference / predicted * 100.0,
            np.nan,
        )
        result[f"{metric}_Previsto"] = predicted
        result[f"{metric}_Aferido"] = measured
        result[f"{metric}_Diferenca"] = difference
        result[f"{metric}_DiferencaPct"] = pct

    return result


def build_global_previsto_aferido(
    predicted_rows: list[Dict[str, float | str]],
    measured_rows: list[Dict[str, float | str]],
) -> pd.DataFrame:
    """Compara os totais previstos e aferidos de cada cenário."""
    predicted = pd.DataFrame(predicted_rows).set_index("Scenario")
    measured = pd.DataFrame(measured_rows).set_index("Scenario")

    rows = []
    for scenario in SCENARIOS:
        row = {"Scenario": scenario}
        for column in ["CPU", "Memoria", "Potencia", "MaiorLatencia"]:
            p = float(predicted.at[scenario, column])
            a = float(measured.at[scenario, column])
            diff = a - p
            row[f"{column}_Previsto"] = p
            row[f"{column}_Aferido"] = a
            row[f"{column}_Diferenca"] = diff
            row[f"{column}_DiferencaPct"] = (
                diff / p * 100.0 if p != 0 and not np.isnan(a) else np.nan
            )
        rows.append(row)
    return pd.DataFrame(rows)


def save_csv(df: pd.DataFrame, path: Path, index: bool = False) -> None:
    df.to_csv(path, index=index, float_format="%.6f")
    print(f"Arquivo gravado: {path}")


if __name__ == "__main__":
    ta = pd.read_csv(
        DIRETORIO_OUT / "ta_RMB.csv",
        dtype={"O-DU": "int64", "O-DU_ID": "int64"},
    )

    df_dm = pd.read_csv(
        DIRETORIO_OUT / "dm_RMB.csv",
        index_col="NumEstacao",
    )
    df_dm.index = df_dm.index.astype(int)
    df_dm.columns = df_dm.columns.astype(int)

    # Estrutura dos cinco cenários.
    associations, structures = load_scenario_associations(ta, df_dm)

    # Custos previstos pela base de caracterização.
    characterization_df = load_characterization_dataframe(
        CHARACTERIZATION_DB_PATH
    )
    empirical_costs = carregar_custos_por_metrica(characterization_df)

    predicted_tables: Dict[str, pd.DataFrame] = {}
    predicted_global_rows = []
    for scenario in SCENARIOS:
        table = build_predicted_odu_table(
            associations[scenario],
            structures[scenario],
            empirical_costs,
        )
        predicted_tables[scenario] = table
        predicted_global_rows.append(
            agregar_metricas_globais(table, scenario)
        )

    # Métricas aferidas no ICARUS.
    execution_df = load_execution_dataframe(
        EXECUTION_DB_PATH,
        structures,
    )
    measured_consolidated = consolidar_metricas_aferidas_por_odu(
        execution_df
    )
    measured_tables = build_measured_odu_tables(
        measured_consolidated,
        structures,
    )

    measured_global_rows = [
        agregar_metricas_globais(measured_tables[scenario], scenario)
        for scenario in SCENARIOS
    ]

    save_csv(
        pd.DataFrame(predicted_global_rows),
        DIRETORIO_OUT / "stats_metricas_previstas_RMB.csv",
    )
    save_csv(
        pd.DataFrame(measured_global_rows),
        DIRETORIO_OUT / "stats_metricas_aferidas_RMB.csv",
    )

    global_comparison = build_global_previsto_aferido(
        predicted_global_rows,
        measured_global_rows,
    )
    save_csv(
        global_comparison,
        DIRETORIO_OUT / "stats_compare_previsto_aferido_global_RMB.csv",
    )

    # 1) Minlink versus cada cenário: resultados realmente aferidos no ICARUS.
    reference_measured = measured_tables["minlink"]
    for scenario in COMPARISON_SCENARIOS:
        comparison = build_minlink_scenario_comparison(
            reference_measured,
            measured_tables[scenario],
            scenario,
        )
        save_csv(
            comparison,
            DIRETORIO_OUT
            / f"stats_compare_aferido_minlink_{scenario}_RMB.csv",
            index=True,
        )

    # 2) Previsto versus aferido: uma comparação por O-DU para cada cenário.
    for scenario in SCENARIOS:
        comparison = build_previsto_aferido_comparison(
            predicted_tables[scenario],
            measured_tables[scenario],
        )
        save_csv(
            comparison,
            DIRETORIO_OUT
            / f"stats_compare_previsto_aferido_{scenario}_RMB.csv",
            index=True,
        )
