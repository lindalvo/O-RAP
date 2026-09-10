import math
import sqlite3
from pathlib import Path

import contextily as ctx
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from shapely import concave_hull
from shapely.geometry import LineString, MultiPoint
from shapely.ops import unary_union

from common.constantes import DIRETORIO_OUT, DP_ROUND_DIGITS, clean_metrics_df


DB_PATH = DIRETORIO_OUT / "metricas.db"
BASEMAP_FILE = DIRETORIO_OUT / "basemap_RMB_osm.tif"

FIGSIZE = (7.2, 7.2)
CONCAVE_HULL_RATIO = 0.35
CLUSTER_BUFFER_M = 260
AREA_BUFFER_M = 650
MAP_PADDING = 0.055

# Métricas existentes na coluna stats.metric e suas assinaturas empíricas.
METRIC_SPECS = {
    "cpu": {
        "metric": "cpu_usage",
        "usar_dp": False,
        "label": "CPU",
        "unit": "%",
    },
    "memory": {
        "metric": "memory_usage",
        "usar_dp": True,
        "label": "Memory",
        "unit": "MB",
    },
    "power": {
        "metric": "cpu_package_power",
        "usar_dp": False,
        "label": "Power",
        "unit": "W",
    },
    "minmaxsched": {
        "metric": "max_scheduler_latency",
        "usar_dp": False,
        "label": "Max sched.",
        "unit": "µs",
    },
}

# Quatro mapas para a referência minlink e um mapa para cada cenário
# especializado, usando a métrica que constitui sua função objetivo.
MAP_SPECS = [
    {"scenario": "minlink", "metric_key": "cpu", "output_suffix": "minlink_cpu"},
    {"scenario": "minlink", "metric_key": "memory", "output_suffix": "minlink_memory"},
    {"scenario": "minlink", "metric_key": "power", "output_suffix": "minlink_power"},
    {
        "scenario": "minlink",
        "metric_key": "minmaxsched",
        "output_suffix": "minlink_minmaxsched",
    },
    {"scenario": "mincpu", "metric_key": "cpu", "output_suffix": "mincpu"},
    {"scenario": "minmemory", "metric_key": "memory", "output_suffix": "minmemory"},
    {"scenario": "minpower", "metric_key": "power", "output_suffix": "minpower"},
    {
        "scenario": "minmaxsched",
        "metric_key": "minmaxsched",
        "output_suffix": "minmaxsched",
    },
]

SCENARIOS = ("minlink", "mincpu", "minmemory", "minpower", "minmaxsched")


def load_metrics_dataframe(db_path):
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

    # Remove potência inválida, corrige overflow de memória e normaliza o DP.
    df = clean_metrics_df(df)
    return df


def consolidar_custos_metricos(df, metric_name, usar_dp):
    """
    Consolida uma métrica na mesma granularidade usada pelos ILPs.

    Primeiro obtém a média das amostras de cada configuração em cada rodada.
    Para CPU, potência e scheduler, configurações que diferem apenas no DP são
    consolidadas na assinatura (fanout, carga agregada). Para memória, o DP é
    mantido e a assinatura é (fanout, carga agregada, DP). Por fim, calcula-se
    a média entre as rodadas.
    """
    stats = df.loc[df["metric"].eq(metric_name)].copy()

    full_key_columns = ["num_orus", "carga_agregada_mhz", "dp_carga_mhz"]
    per_configuration_round = (
        stats.groupby(
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


def carregar_custos_por_metrica(df):
    """Constrói uma tabela de custos empíricos para cada métrica dos mapas."""
    return {
        metric_key: consolidar_custos_metricos(
            df,
            spec["metric"],
            usar_dp=spec["usar_dp"],
        )
        for metric_key, spec in METRIC_SPECS.items()
    }


def calcular_assinaturas_odus(clusters):
    """Calcula fanout, carga agregada e DP das cargas de cada O-DU."""
    signatures = []

    for du, group in clusters.groupby("O-DU", sort=True):
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


def estimar_metrica_por_odu(clusters, custos, usar_dp):
    """Obtém do banco o custo empírico correspondente à assinatura de cada O-DU."""
    signatures = calcular_assinaturas_odus(clusters)

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

    estimates = {}
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


def format_metric_value(value, metric_key):
    """Formata o valor exibido junto ao identificador da O-DU."""
    spec = METRIC_SPECS[metric_key]
    return f'{spec["label"]}: {value:.2f} {spec["unit"]}'


def create_cluster_boundary(points):
    """Cria o contorno do cluster, inclusive para clusters com poucos pontos."""
    points = list(points)

    if len(points) == 1:
        return points[0].buffer(CLUSTER_BUFFER_M)

    if len(points) == 2:
        return LineString(points).buffer(
            CLUSTER_BUFFER_M,
            cap_style="round",
            join_style="round",
        )

    multipoint = MultiPoint(points)
    boundary = concave_hull(
        multipoint,
        ratio=CONCAVE_HULL_RATIO,
        allow_holes=False,
    )

    if boundary.geom_type not in {"Polygon", "MultiPolygon"}:
        boundary = multipoint.convex_hull

    return boundary.buffer(CLUSTER_BUFFER_M, join_style="round")


def add_scale_bar(ax, bounds):
    """Adiciona uma barra de escala simples em metros ou quilômetros."""
    minx, miny, maxx, maxy = bounds
    width = maxx - minx
    height = maxy - miny

    target = width * 0.18
    options = np.array([100, 200, 500, 1000, 2000, 5000, 10000, 20000])
    valid = options[options <= target]
    length = int(valid[-1] if len(valid) else options[0])

    x = minx + width * 0.06
    y = miny + height * 0.055
    tick = height * 0.007

    ax.plot([x, x + length], [y, y], color="black", linewidth=1.4, zorder=20)
    ax.plot([x, x], [y - tick, y + tick], color="black", linewidth=1.0, zorder=20)
    ax.plot(
        [x + length, x + length],
        [y - tick, y + tick],
        color="black",
        linewidth=1.0,
        zorder=20,
    )

    label = f"{length / 1000:g} km" if length >= 1000 else f"{length} m"
    ax.text(
        x + length / 2,
        y + height * 0.013,
        label,
        ha="center",
        va="bottom",
        fontsize=7,
    )


def add_north_arrow(ax):
    """Adiciona indicação discreta de norte."""
    ax.annotate(
        "N",
        xy=(0.94, 0.94),
        xytext=(0.94, 0.86),
        xycoords="axes fraction",
        textcoords="axes fraction",
        ha="center",
        va="center",
        fontsize=9,
        fontweight="bold",
        arrowprops={
            "arrowstyle": "-|>",
            "facecolor": "black",
            "edgecolor": "black",
            "linewidth": 1.0,
        },
        zorder=30,
    )


def add_openstreetmap_basemap(ax, base_geo, metric_crs, basemap_file):
    """
    Adiciona uma base cartográfica do OpenStreetMap.

    O arquivo GeoTIFF é baixado somente quando ainda não existe.
    Nas execuções seguintes, o arquivo local é reutilizado.
    """
    basemap_file = Path(basemap_file)

    if not basemap_file.exists():
        print(f"Baixando mapa-base do OpenStreetMap para {basemap_file}")

        # O contextily trabalha nativamente com tiles em Web Mercator.
        base_web = base_geo.to_crs("EPSG:3857")

        web_area = unary_union(
            base_web.geometry.tolist()
        ).convex_hull.buffer(AREA_BUFFER_M)

        minx, miny, maxx, maxy = web_area.bounds
        headers = {
            "User-Agent": (
                "O-RAP-O-RAN-Research/1.0 "
                "(academic map generation; "
                "contact: netobrpa@gmail.com)"
            )
        }
        ctx.bounds2raster(
            minx,
            miny,
            maxx,
            maxy,
            path=basemap_file,
            zoom=11,
            source=ctx.providers.OpenStreetMap.Mapnik,
            headers=headers,
            n_connections=1,
            use_cache=True,
            wait=1,
            max_retries=2,
        )

    print(f"Carregando mapa-base local {basemap_file}")

    ctx.add_basemap(
        ax,
        crs=metric_crs,
        source=basemap_file,
        alpha=0.48,
        reset_extent=True,
        zorder=0,
        attribution=False,
    )

    ctx.add_attribution(
        ax,
        "© OpenStreetMap contributors",
        font_size=6,
    )


def generate_map(base, clusters, estimates, metric_key, output):
    """
    Gera um mapa PDF de um cenário, exibindo O-DU_ID e custo empírico.

    Parameters
    ----------
    base : pandas.DataFrame
        Base comum com NumEstacao, Lat e Lon.
    clusters : pandas.DataFrame
        Resultado ILP com NumEstacao, Lat, Lon, bandwidth, O-DU e O-DU_ID.
    estimates : dict[int, float]
        Valor empírico estimado para cada O-DU longa.
    metric_key : str
        Chave em METRIC_SPECS usada para rótulo e unidade.
    output : pathlib.Path
        Caminho do PDF de saída.
    """
    output = Path(output)

    base = base.copy()
    clusters = clusters.copy()

    base["NumEstacao"] = base["NumEstacao"].astype("int64")
    clusters["NumEstacao"] = clusters["NumEstacao"].astype("int64")
    clusters["O-DU"] = clusters["O-DU"].astype("int64")
    clusters["O-DU_ID"] = clusters["O-DU_ID"].astype("int64")

    base_geo = gpd.GeoDataFrame(
        base,
        geometry=gpd.points_from_xy(base["Lon"], base["Lat"]),
        crs="EPSG:4326",
    )

    metric_crs = base_geo.estimate_utm_crs()
    base_geo = base_geo.to_crs(metric_crs)

    clusters_geo = gpd.GeoDataFrame(
        clusters,
        geometry=gpd.points_from_xy(clusters["Lon"], clusters["Lat"]),
        crs="EPSG:4326",
    ).to_crs(metric_crs)

    # O enquadramento é calculado exclusivamente pela base comum.
    area = unary_union(base_geo.geometry.tolist()).convex_hull.buffer(
        AREA_BUFFER_M,
        join_style="round",
    )

    minx, miny, maxx, maxy = area.bounds
    padding = max(maxx - minx, maxy - miny) * MAP_PADDING
    bounds = (
        minx - padding,
        miny - padding,
        maxx + padding,
        maxy + padding,
    )

    cluster_ids = sorted(clusters_geo["O-DU"].unique())
    colors = plt.get_cmap("tab20", len(cluster_ids))
    cluster_colors = {
        cluster_id: colors(index)
        for index, cluster_id in enumerate(cluster_ids)
    }

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 8,
            "pdf.fonttype": 42,
        }
    )

    fig, ax = plt.subplots(figsize=FIGSIZE)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#fafafa")

    # Base cartográfica discreta: ruas e massas d'água, sem rótulos.
    # Os mosaicos são rasterizados dentro do PDF; os clusters permanecem vetoriais.
    ax.set_xlim(bounds[0], bounds[2])
    ax.set_ylim(bounds[1], bounds[3])
    add_openstreetmap_basemap(
        ax=ax,
        base_geo=base_geo,
        metric_crs=metric_crs,
        basemap_file=BASEMAP_FILE,
    )

    # Limite simplificado da área analisada.
    gpd.GeoSeries([area], crs=metric_crs).plot(
        ax=ax,
        facecolor="none",
        edgecolor="#888888",
        linewidth=0.7,
        linestyle=(0, (3, 2)),
        zorder=1,
    )

    for cluster_id, group in clusters_geo.groupby("O-DU", sort=True):
        color = cluster_colors[cluster_id]
        boundary = create_cluster_boundary(group.geometry)

        # Preenchimento e contorno do cluster.
        gpd.GeoSeries([boundary], crs=metric_crs).plot(
            ax=ax,
            facecolor=color,
            edgecolor="none",
            alpha=0.10,
            zorder=2,
        )
        gpd.GeoSeries([boundary], crs=metric_crs).boundary.plot(
            ax=ax,
            color=color,
            linewidth=1.0,
            alpha=0.90,
            zorder=3,
        )

        du = group.loc[group["NumEstacao"] == cluster_id].iloc[0]
        du_point = du.geometry

        # Enlaces O-DU–O-RU.
        for _, ru in group.iterrows():
            if ru["NumEstacao"] == cluster_id:
                continue

            ax.plot(
                [du_point.x, ru.geometry.x],
                [du_point.y, ru.geometry.y],
                color=color,
                linewidth=0.55,
                alpha=0.40,
                zorder=4,
            )

        # O-RUs.
        rus = group.loc[group["NumEstacao"] != cluster_id]
        if not rus.empty:
            rus.plot(
                ax=ax,
                marker="o",
                markersize=18,
                color=color,
                edgecolor="white",
                linewidth=0.4,
                zorder=6,
            )

        # O-DU e rótulo com identificador sequencial + custo empírico.
        ax.scatter(
            du_point.x,
            du_point.y,
            marker="s",
            s=54,
            facecolor=color,
            edgecolor="black",
            linewidth=0.8,
            zorder=10,
        )

        metric_text = format_metric_value(
            estimates[int(cluster_id)],
            metric_key,
        )
        annotation = f'O-DU {int(du["O-DU_ID"])}\n{metric_text}'

        ax.annotate(
            annotation,
            xy=(du_point.x, du_point.y),
            xytext=(5, 5),
            textcoords="offset points",
            fontsize=5.3,
            fontweight="semibold",
            linespacing=1.05,
            ha="left",
            va="bottom",
            bbox={
                "boxstyle": "round,pad=0.13",
                "facecolor": "white",
                "edgecolor": "none",
                "alpha": 0.84,
            },
            zorder=12,
        )

    ax.set_xlim(bounds[0], bounds[2])
    ax.set_ylim(bounds[1], bounds[3])
    ax.set_aspect("equal", adjustable="box")

    # Remove eixos e coordenadas para manter a figura limpa.
    ax.tick_params(
        left=False,
        bottom=False,
        labelleft=False,
        labelbottom=False,
    )
    for spine in ax.spines.values():
        spine.set_visible(False)

    legend = [
        Line2D(
            [0], [0],
            marker="o",
            linestyle="none",
            markerfacecolor="#777777",
            markeredgecolor="white",
            markersize=5.5,
            label="O-RU",
        ),
        Line2D(
            [0], [0],
            marker="s",
            linestyle="none",
            markerfacecolor="#777777",
            markeredgecolor="black",
            markersize=6.5,
            label="O-DU",
        ),
        Line2D(
            [0], [0],
            color="#777777",
            linewidth=0.8,
            label="Network Link O-DU<–>O-RU",
        ),
        Patch(
            facecolor="#bbbbbb",
            edgecolor="#777777",
            alpha=0.25,
            label="Cluster",
        ),
        Line2D(
            [0], [0],
            color="#aaaaaa",
            linewidth=0.8,
            linestyle=(0, (3, 2)),
            label="Area analyzed",
        ),
    ]

    ax.legend(
        handles=legend,
        loc="lower right",
        frameon=True,
        framealpha=0.92,
        facecolor="white",
        edgecolor="#bbbbbb",
        fontsize=7,
        borderpad=0.5,
        labelspacing=0.4,
        handlelength=2.0,
    )

    add_scale_bar(ax, bounds)
    add_north_arrow(ax)

    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        output,
        format="pdf",
        bbox_inches="tight",
        pad_inches=0.06,
        facecolor="white",
    )
    plt.close(fig)

    print(f"Mapa gerado: {output}")
    return True


def main():
    # Base geográfica comum às cinco soluções.
    csv_path = DIRETORIO_OUT / "grp_RMB.csv"
    print(f"Carregando o arquivo {csv_path}")
    base = pd.read_csv(csv_path)

    # ta_RMB.csv apenas converte o identificador longo da O-DU em O-DU_ID.
    ta = pd.read_csv(DIRETORIO_OUT / "ta_RMB.csv")

    print(f"Carregando métricas empíricas de {DB_PATH}")
    metrics_df = load_metrics_dataframe(DB_PATH)
    costs_by_metric = carregar_custos_por_metrica(metrics_df)

    # Lê exatamente os cinco cenários utilizados no estudo.
    clusters_by_scenario = {}
    for scenario in SCENARIOS:
        scenario_path = DIRETORIO_OUT / f"ilp_RMB_{scenario}.csv"
        print(f"Carregando o arquivo {scenario_path}")
        clusters = pd.read_csv(scenario_path)
        clusters = clusters.merge(ta, on="O-DU", how="left", validate="m:1")
        clusters_by_scenario[scenario] = clusters

    # Gera os quatro mapas de referência e os quatro mapas especializados.
    for map_spec in MAP_SPECS:
        scenario = map_spec["scenario"]
        metric_key = map_spec["metric_key"]
        metric_spec = METRIC_SPECS[metric_key]
        clusters = clusters_by_scenario[scenario]

        estimates = estimar_metrica_por_odu(
            clusters,
            costs_by_metric[metric_key],
            usar_dp=metric_spec["usar_dp"],
        )

        output = (
            DIRETORIO_OUT
            / f'map_ilp_metric_RMB_{map_spec["output_suffix"]}.pdf'
        )
        generate_map(
            base=base,
            clusters=clusters,
            estimates=estimates,
            metric_key=metric_key,
            output=output,
        )


if __name__ == "__main__":
    main()
