import math
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd
from common.constantes import (
    DIRETORIO_OUT,
    DP_ROUND_DIGITS,
    clean_metrics_df,
)


DB_PATH = DIRETORIO_OUT / "metricas.db"

METRIC_SPECS = {
    "cpu": {
        "metric": "cpu_usage",
        "usar_dp": False,
        "column": "CPU",
        "odu_column": "CPU",
        "aggregation": "sum",
    },
    "memory": {
        "metric": "memory_usage",
        "usar_dp": True,
        "column": "Memoria",
        "odu_column": "Memoria",
        "aggregation": "sum",
    },
    "power": {
        "metric": "cpu_package_power",
        "usar_dp": False,
        "column": "Potencia",
        "odu_column": "Potencia",
        "aggregation": "sum",
    },
    "minmaxsched": {
        "metric": "max_scheduler_latency",
        "usar_dp": False,
        "column": "MaiorLatencia",
        "odu_column": "MaxSchedulerLatency",
        "aggregation": "max",
    },
}

# Nome dos cinco cenários esperados nos arquivos ilp_RMB_*.csv.
SCENARIO_CHAINS = {
    "minlink",
    "mincpu",
    "minmemory",
    "minpower",
    "minmaxsched",
}

COMPARISON_SCENARIOS = (
    "mincpu",
    "minmemory",
    "minpower",
    "minmaxsched",
)


def load_metrics_dataframe(db_path: Path) -> pd.DataFrame:
    """Lê a tabela stats e aplica a limpeza/normalização comum do projeto."""
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

    df = clean_metrics_df(df)
    return df


def consolidar_custos_metricos(
    df: pd.DataFrame,
    metric_name: str,
    usar_dp: bool,
) -> pd.DataFrame:
    """Consolida os custos empíricos na granularidade usada pelos ILPs."""
    metric_df = df.loc[df["metric"].eq(metric_name)].copy()

    full_key_columns = [
        "num_orus",
        "carga_agregada_mhz",
        "dp_carga_mhz",
    ]
    per_configuration_round = (
        metric_df.groupby(
            full_key_columns + ["roundtrip", "metric"],
            as_index=False,
        )["value"]
        .mean()
    )

    if usar_dp:
        key_columns = full_key_columns
        per_round = per_configuration_round
    else:
        key_columns = ["num_orus", "carga_agregada_mhz"]
        per_round = (
            per_configuration_round.groupby(
                key_columns + ["roundtrip", "metric"],
                as_index=False,
            )["value"]
            .mean()
        )

    consolidated = (
        per_round.groupby(key_columns + ["metric"], as_index=False)["value"]
        .mean()
        .rename(columns={"value": "custo_empirico"})
    )

    consolidated["num_orus"] = consolidated["num_orus"].astype(int)
    consolidated["carga_agregada_mhz"] = (
        consolidated["carga_agregada_mhz"].astype(int)
    )

    return consolidated.sort_values(key_columns).reset_index(drop=True)


def carregar_custos_por_metrica(
    df: pd.DataFrame,
) -> Dict[str, pd.DataFrame]:
    """Constrói as tabelas empíricas das quatro métricas."""
    return {
        metric_key: consolidar_custos_metricos(
            df,
            spec["metric"],
            usar_dp=spec["usar_dp"],
        )
        for metric_key, spec in METRIC_SPECS.items()
    }


def calcular_assinaturas_odus(df: pd.DataFrame) -> pd.DataFrame:
    """Calcula fanout, carga agregada e DP das cargas de cada O-DU."""
    signatures = []

    for du, group in df.groupby("O-DU", sort=True):
        loads = group["bandwidth"].astype(float).tolist()
        num_orus = len(loads)
        total_load = int(round(sum(loads)))
        mean_load = total_load / num_orus
        dp_load = round(
            math.sqrt(
                sum((load - mean_load) ** 2 for load in loads) / num_orus
            ),
            DP_ROUND_DIGITS,
        )
        signatures.append(
            {
                "O-DU": int(du),
                "num_orus": num_orus,
                "carga_agregada_mhz": total_load,
                "dp_carga_mhz": dp_load,
            }
        )

    return pd.DataFrame(signatures)


def estimar_metrica_por_odu(
    signatures: pd.DataFrame,
    custos: pd.DataFrame,
    usar_dp: bool,
) -> Dict[int, float]:
    """Obtém o custo empírico correspondente à assinatura de cada O-DU."""
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
            (
                int(row.num_orus),
                int(row.carga_agregada_mhz),
            ): float(row.custo_empirico)
            for row in custos.itertuples(index=False)
        }

    estimates: Dict[int, float] = {}
    for row in signatures.itertuples(index=False):
        signature = (
            (
                int(row.num_orus),
                int(row.carga_agregada_mhz),
                round(float(row.dp_carga_mhz), DP_ROUND_DIGITS),
            )
            if usar_dp
            else (
                int(row.num_orus),
                int(row.carga_agregada_mhz),
            )
        )
        estimates[int(row._0)] = cost_index[signature]

    return estimates


def estimar_metricas_cenario(
    df: pd.DataFrame,
    scenario: str,
    custos_por_metrica: Dict[str, pd.DataFrame],
) -> Dict[str, Any]:
    """Calcula os quatro custos globais previstos de um cenário."""
    signatures = calcular_assinaturas_odus(df)
    result: Dict[str, Any] = {"Scenario": scenario}

    for metric_key, spec in METRIC_SPECS.items():
        estimates = estimar_metrica_por_odu(
            signatures=signatures,
            custos=custos_por_metrica[metric_key],
            usar_dp=spec["usar_dp"],
        )
        values = list(estimates.values())
        result[spec["column"]] = (
            float(sum(values))
            if spec["aggregation"] == "sum"
            else float(max(values))
        )

    return result


def build_odu_evaluation_table(
    df: pd.DataFrame,
    custos_por_metrica: Dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """
    Gera uma linha por O-DU com a estrutura do agrupamento e os quatro
    custos empíricos previstos para a respectiva configuração.

    O desvio padrão usa a população das cargas das O-RUs da O-DU,
    exatamente como na assinatura empírica utilizada pelo ILP.
    """
    signatures = calcular_assinaturas_odus(df).set_index("O-DU")

    grouped = (
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

    grouped["O-DU_ID"] = grouped["O-DU_ID"].astype(int)
    grouped["NumRUs"] = grouped["NumRUs"].astype(int)
    grouped["TotalLinkDistanceKM"] = grouped["TotalLinkDistanceKM"].astype(float)
    grouped["AggregatedLoadMHz"] = grouped["AggregatedLoadMHz"].astype(float)
    grouped["DPLoadMHz"] = signatures["dp_carga_mhz"].astype(float)

    signatures_for_lookup = signatures.reset_index()
    for metric_key, spec in METRIC_SPECS.items():
        estimates = estimar_metrica_por_odu(
            signatures=signatures_for_lookup,
            custos=custos_por_metrica[metric_key],
            usar_dp=spec["usar_dp"],
        )
        grouped[spec["odu_column"]] = pd.Series(estimates, dtype=float)

    grouped.index = grouped.index.astype(int)
    grouped.index.name = "O-DU"
    return grouped


def build_scenario_comparison(
    reference: pd.DataFrame,
    scenario: pd.DataFrame,
    reference_name: str = "minlink",
    scenario_name: str = "scenario",
) -> pd.DataFrame:
    """
    Coloca lado a lado os valores por O-DU do minlink e de um cenário
    orientado a recurso, facilitando a comparação de cada agrupamento.
    """
    fields = [
        "NumRUs",
        "TotalLinkDistanceKM",
        "AggregatedLoadMHz",
        "DPLoadMHz",
        "CPU",
        "Memoria",
        "Potencia",
        "MaxSchedulerLatency",
    ]

    result = pd.DataFrame(index=reference.index.copy())
    result.index.name = "O-DU"
    result["O-DU_ID"] = reference["O-DU_ID"].astype(int)

    for field in fields:
        result[f"{reference_name}_{field}"] = reference[field]
        result[f"{scenario_name}_{field}"] = scenario[field]

    return result

def add_link_distance(
    df: pd.DataFrame,
    df_dm: pd.DataFrame,
) -> pd.DataFrame:
    """
    Adiciona a distância RU -> DU de cada associação.
    """
    result = df.copy()
    result["NumEstacao"] = result["NumEstacao"].astype(int)
    result["O-DU"] = result["O-DU"].astype(int)
    result["bandwidth"] = pd.to_numeric(result["bandwidth"],errors="raise").astype(float)
    result["LinkDistanceKM"] = [
        float(df_dm.at[ru_id, du_id])
        for ru_id, du_id in zip(
            result["NumEstacao"],
            result["O-DU"],
        )
    ]

    return result


def stats(
    df: pd.DataFrame,
    scenario: str,
) -> Dict[str, Any]:
    """
    Calcula e retorna as estatísticas gerais de um cenário.
    O DataFrame deve conter a coluna LinkDistanceKM.
    """
    dist_ru_du = df["LinkDistanceKM"].to_numpy(dtype=float)

    # As associações O-DU -> própria O-DU contribuem com distância zero.
    total_enlace_km = float(dist_ru_du.sum())

    # Exclui as associações locais para média e desvio padrão dos enlaces.
    dist_links = df.loc[
        df["NumEstacao"] != df["O-DU"],
        "LinkDistanceKM",
    ].to_numpy(dtype=float)

    if len(dist_links) == 0:
        media_dist_ru_du = 0.0
        dp_dist_ru_du = 0.0
    elif len(dist_links) == 1:
        media_dist_ru_du = float(dist_links.mean())
        dp_dist_ru_du = 0.0
    else:
        media_dist_ru_du = float(dist_links.mean())
        dp_dist_ru_du = float(dist_links.std(ddof=1))

    qtde_dus = int(df["O-DU"].nunique())

    # Inclui a O-RU co-localizada com a própria O-DU.
    rus_por_du = (
        df.groupby("O-DU")["NumEstacao"]
        .count()
        .astype(int)
    )

    media_qtde_ru_du = float(rus_por_du.mean())
    dp_qtde_ru_du = (
        float(rus_por_du.std(ddof=1))
        if len(rus_por_du) > 1
        else 0.0
    )

    bandwidth_por_du = (
        df.groupby("O-DU")["bandwidth"]
        .sum()
        .astype(float)
    )

    media_bandwidth_du = float(bandwidth_por_du.mean())
    dp_bandwidth_du = (
        float(bandwidth_por_du.std(ddof=1))
        if len(bandwidth_por_du) > 1
        else 0.0
    )

    qtde_pontos = int(len(df))

    print(f"\n--- Estatísticas da Clusterização: {scenario} ---")
    print(f"Quantidade total de pontos (RUs + DUs): {qtde_pontos}")
    print(f"Quantidade de DUs (clusters): {qtde_dus}")
    print(
        f"Média de RUs por DU (cluster): "
        f"{media_qtde_ru_du:.2f} "
        f"(DP: {dp_qtde_ru_du:.2f})"
    )
    print(
        f"Média de Largura de Banda por DU "
        f"(carga total do cluster): "
        f"{media_bandwidth_du:.2f} "
        f"(DP: {dp_bandwidth_du:.2f})"
    )
    print(
        f"Distância total de enlace (soma RU->DU): "
        f"{total_enlace_km:.2f} km"
    )
    print(
        f"Média de distância RU->DU "
        f"(excluindo DUs ligadas a si mesmas): "
        f"{media_dist_ru_du:.2f} km "
        f"(DP: {dp_dist_ru_du:.2f} km)"
    )

    return {
        "Scenario": scenario,
        "TotalPoints": qtde_pontos,
        "NumDUs": qtde_dus,
        "MediaRUsPerDU": media_qtde_ru_du,
        "DP_RUsPerDU": dp_qtde_ru_du,
        "MediaBandwidthPerDU": media_bandwidth_du,
        "DP_BandwidthPerDU": dp_bandwidth_du,
        "TotalLinkDistanceKM": total_enlace_km,
        "MediaLinkDistance": media_dist_ru_du,
        "DP_LinkDistance": dp_dist_ru_du,
    }


def stats_by_odu(
    df: pd.DataFrame,
    scenario: str,
) -> pd.DataFrame:
    """
    Gera as estatísticas por O-DU de um cenário.

    Retorna uma linha por O-DU, contendo:
    - quantidade de O-RUs associadas;
    - soma das distâncias RU -> DU do cluster.
    """
    grouped = (
        df.groupby("O-DU", sort=True)
        .agg(
            NumRUs=("NumEstacao", "count"),
            TotalLinkDistanceKM=("LinkDistanceKM", "sum"),
            AggregatedLoadMHz=("bandwidth", "sum")
        )
    )

    grouped["NumRUs"] = grouped["NumRUs"].astype(int)
    grouped["TotalLinkDistanceKM"] = grouped["TotalLinkDistanceKM"].astype(float)
    grouped["AggregatedLoadMHz"] = grouped["AggregatedLoadMHz"].astype(float)

    return grouped.rename(
        columns={
            "NumRUs": f"{scenario}_NumRUs",
            "TotalLinkDistanceKM": (
                f"{scenario}_TotalLinkDistanceKM"
            ),
            "AggregatedLoadMHz": (
                f"{scenario}_AggregatedLoadMHz"
            )
        }
    )


def build_stats_by_odus(
    tables_by_scenario: Dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """
    Combina horizontalmente os resultados por O-DU de todos os cenários
    e adiciona uma linha final TOTAL.
    """
    if not tables_by_scenario:
        return pd.DataFrame()

    scenarios = list(tables_by_scenario)
    reference_scenario = scenarios[0]
    reference_odus = set(tables_by_scenario[reference_scenario].index)

    # Como as localizações de O-DU devem ser iguais, uma diferença é erro.
    for scenario in scenarios[1:]:
        current_odus = set(tables_by_scenario[scenario].index)

        if current_odus != reference_odus:
            missing = sorted(reference_odus - current_odus)
            extra = sorted(current_odus - reference_odus)
            raise ValueError(
                "Os conjuntos de O-DUs não são iguais entre os cenários. "
                f"Referência: {reference_scenario}; "
                f"cenário divergente: {scenario}; "
                f"ausentes: {missing}; adicionais: {extra}."
            )

    result = pd.concat(
        [tables_by_scenario[s] for s in scenarios],
        axis=1,
    ).sort_index()

    result.index = result.index.astype(object)

    total_row: Dict[str, Any] = {}
    for scenario in scenarios:
        num_rus_col = f"{scenario}_NumRUs"
        distance_col = f"{scenario}_TotalLinkDistanceKM"
        load_col = f"{scenario}_AggregatedLoadMHz"
        total_row[num_rus_col] = int(result[num_rus_col].sum())
        total_row[distance_col] = float(result[distance_col].sum())
        total_row[load_col] = float(result[load_col].sum())

    result.loc["TOTAL"] = total_row
    result.index.name = "O-DU"

    return result


if __name__ == "__main__":
    # A matriz é carregada apenas uma vez e reutilizada em todos os cenários.
    dm_path = DIRETORIO_OUT / f"dm_RMB.csv"
    ta = pd.read_csv(DIRETORIO_OUT / f"ta_RMB.csv", dtype={"O-DU": "int64","O-DU_ID": "int64",},)
    if not dm_path.exists():
        raise FileNotFoundError(
            f"Matriz de distâncias não encontrada: {dm_path}"
        )

    df_dm = pd.read_csv(
        dm_path,
        index_col="NumEstacao",
    )

    df_dm.index = df_dm.index.astype(int)
    df_dm.columns = df_dm.columns.astype(int)

    print(f"Carregando métricas empíricas de {DB_PATH}")
    metrics_df = load_metrics_dataframe(DB_PATH)
    custos_por_metrica = carregar_custos_por_metrica(metrics_df)

    stats_rows: List[Dict[str, Any]] = []
    metric_rows: List[Dict[str, Any]] = []
    odu_tables: Dict[str, pd.DataFrame] = {}
    odu_evaluation_tables: Dict[str, pd.DataFrame] = {}

    # Lê todos os arquivos de associação dos cenários/heurísticas.
    padrao = "ilp_RMB_*.csv"

    for arquivo_csv in sorted(DIRETORIO_OUT.glob(padrao)):
        print(f"Carregando o arquivo {arquivo_csv}")
        clusters = pd.read_csv(arquivo_csv)
        clusters["O-DU"] = clusters["O-DU"].astype("int64")
        clusters = clusters.merge(ta[["O-DU", "O-DU_ID"]],on="O-DU",how="left",validate="m:1")
        cadeia = arquivo_csv.stem.split("ilp_RMB_",1,)[1]
        if cadeia not in SCENARIO_CHAINS:
            continue

        scenario = f"ilp_{cadeia}"

        clusters = add_link_distance(
            df=clusters,
            df_dm=df_dm,
        )

        stats_rows.append(
            stats(
                df=clusters,
                scenario=scenario,
            )
        )

        odu_tables[scenario] = stats_by_odu(
            df=clusters,
            scenario=scenario,
        )

        odu_evaluation_tables[cadeia] = build_odu_evaluation_table(
            df=clusters,
            custos_por_metrica=custos_por_metrica,
        )

        metric_rows.append(
            estimar_metricas_cenario(
                df=clusters,
                scenario=scenario,
                custos_por_metrica=custos_por_metrica,
            )
        )

    if not stats_rows:
        raise FileNotFoundError(
            "Nenhum arquivo de associações foi encontrado para os "
            f"padrões ilp_RMB_*.csv ou grd_RMB_*.csv "
            f"em {DIRETORIO_OUT}."
        )

    # Estatísticas gerais: uma linha por cenário.
    stats_df = pd.DataFrame(stats_rows)
    stats_output = DIRETORIO_OUT / f"stats_RMB.csv"
    stats_df.to_csv(stats_output, index=False)

    print(f"\nEstatísticas gerais gravadas em: {stats_output}")

    # Custos empíricos previstos: uma linha por cenário.
    metrics_stats_df = pd.DataFrame(
        metric_rows,
        columns=[
            "Scenario",
            "CPU",
            "Memoria",
            "Potencia",
            "MaiorLatencia",
        ],
    )
    metrics_stats_output = DIRETORIO_OUT / "stats_metricas_RMB.csv"
    metrics_stats_df.to_csv(
        metrics_stats_output,
        index=False,
        float_format="%.6f",
    )

    print(
        "Custos empíricos previstos gravados em: "
        f"{metrics_stats_output}"
    )

    # Comparações individuais por O-DU: minlink versus cada cenário de recurso.
    reference_table = odu_evaluation_tables["minlink"]
    for comparison_scenario in COMPARISON_SCENARIOS:
        comparison_df = build_scenario_comparison(
            reference=reference_table,
            scenario=odu_evaluation_tables[comparison_scenario],
            reference_name="minlink",
            scenario_name=comparison_scenario,
        )
        comparison_output = (
            DIRETORIO_OUT
            / f"stats_compare_minlink_{comparison_scenario}_RMB.csv"
        )
        comparison_df.to_csv(
            comparison_output,
            index=True,
            float_format="%.6f",
        )
        print(
            "Comparação por O-DU gravada em: "
            f"{comparison_output}"
        )

    # Estatísticas por O-DU: uma linha por O-DU e colunas por cenário.
    stats_by_odus_df = build_stats_by_odus(odu_tables)

    odu_id_map = ta.set_index("O-DU")["O-DU_ID"]
    odu_ids = (stats_by_odus_df.index.to_series().map(odu_id_map).astype("Int64"))

    stats_by_odus_df.insert(0,"O-DU_ID",odu_ids,)
    stats_by_odus_output = (DIRETORIO_OUT / f"stats_by_odus_RMB.csv")

    stats_by_odus_df.to_csv(stats_by_odus_output,index=True,float_format="%.6f")

    print("Estatísticas por O-DU gravadas em: "f"{stats_by_odus_output}")
