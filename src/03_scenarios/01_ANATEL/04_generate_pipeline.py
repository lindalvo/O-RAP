import csv
import os
from pathlib import Path
import pandas as pd

DIRETORIO_MAIN = Path(__file__).resolve().parent
DIRETORIO_OUT = (DIRETORIO_MAIN / "../../OUT").resolve()
FIBER_DELAY_US_PER_KM = float(5.0)  # atraso de propagação em microssegundos por km de fibra óptica
SCENARIO_SEPARATOR = "|"


def merge_equal_associations(rows: list[list]) -> list[list]:
    """
    Une linhas com a mesma associação O-DU/O-RU.

    O cenário não participa da comparação. Quando uma associação pertence a
    mais de um cenário, a primeira coluna recebe todos os seus cenários
    separados por ``|``. O restante da linha permanece inalterado.
    """
    associations = {}

    for row in rows:
        scenario = str(row[0])
        association = tuple(row[1:])

        if association not in associations:
            associations[association] = []

        if scenario not in associations[association]:
            associations[association].append(scenario)

    return [
        [SCENARIO_SEPARATOR.join(scenarios), *association]
        for association, scenarios in associations.items()
    ]

def generate_csv_to_pipeline(
    df: pd.DataFrame,
    df_dm: pd.DataFrame,
    scenario: str
) -> list[list]:
    """
    Gera as linhas de pipeline de um cenário, uma por cluster O-DU/O-RU.

    Formato de cada linha:
        CENARIO1|CENARIO2, NUMESTACAO_DU, BW_DU, 0,NUMESTACAO_RU_1, BW_RU_1, DELAY_RU_1_US,NUMESTACAO_RU_2, BW_RU_2, DELAY_RU_2_US, ...

    O primeiro trio representa a própria O-RU promovida a O-DU:
        - NumEstacao da O-DU;
        - largura de banda da O-DU;
        - delay igual a zero.

    Cada trio seguinte representa uma O-RU atendida pela O-DU:
        - NumEstacao da O-RU;
        - largura de banda da O-RU;
        - delay em microssegundos, calculado a partir da distância até a O-DU.

    O delay é calculado por:
        delay_us = distancia_km * FIBER_DELAY_US_PER_KM

    Parâmetros:
    - df: DataFrame com as colunas NumEstacao, bandwidth e O-DU.
    - df_dm: matriz de distâncias indexada por NumEstacao nas linhas e colunas.
    - scenario: nome do cenário, incluído na primeira coluna de cada linha.

    Retorno:
    - lista com as linhas que serão gravadas no arquivo único de pipeline.
    """
    

    df = df.copy()
    df["NumEstacao"] = df["NumEstacao"].astype(int)
    df["O-DU"] = df["O-DU"].astype(int)

    df_dm = df_dm.copy()
    df_dm.index = df_dm.index.astype(int)
    df_dm.columns = df_dm.columns.astype(int)

    # Mantém a ordem em que as DUs aparecem no arquivo de clusterização.
    du_ids = df.loc[df["NumEstacao"] == df["O-DU"], "NumEstacao"].tolist()

    rows = []

    for du_id in du_ids:
        du_row = df.loc[df["NumEstacao"] == du_id].iloc[0]
        du_bandwidth = int(du_row["bandwidth"])

        # Primeiro trio: O-RU que foi promovida a O-DU.
        row = [
            scenario,
            int(du_id),
            du_bandwidth,
            0,
        ]

        # Demais O-RUs associadas à O-DU, preservando a ordem do DataFrame.
        cluster_rus = df.loc[
            (df["O-DU"] == du_id) & (df["NumEstacao"] != du_id)
        ].sort_values("NumEstacao")

        for _, ru_row in cluster_rus.iterrows():
            ru_id = int(ru_row["NumEstacao"])
            ru_bandwidth = int(ru_row["bandwidth"])
            distance_km = float(df_dm.loc[ru_id, du_id])
            delay_us = int(round(distance_km * FIBER_DELAY_US_PER_KM))

            row.extend([
                ru_id,
                ru_bandwidth,
                delay_us,
            ])

        rows.append(row)

    print(
        f"Cenário {scenario}: {len(rows)} clusters e "
        f"{len(df)} estações."
    )
    return rows
    
if __name__ == "__main__":
    csv_path = DIRETORIO_OUT / f"dm_RMB.csv"
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Matriz de distâncias não encontrada: {csv_path}")
    # Carrega a Matriz de Distâncias. Define 'id' como índice para acesso rápido: dists.loc[id_origem, id_destino]
    df_dm = pd.read_csv(csv_path, index_col='NumEstacao')
    df_dm.index = df_dm.index.astype(int)
    df_dm.columns = df_dm.columns.astype(int)

    padrao = "ilp_RMB_*.csv"
    output_filename = DIRETORIO_OUT / "pipeline_RMB.txt"
    rows = []

    for arquivo_csv in DIRETORIO_OUT.glob(padrao):
        #abrindo o arquivo de  clusterização
        csv_path = arquivo_csv
        print(f"Carregando o arquivo {csv_path}")
        scenario = arquivo_csv.stem.split("ilp_RMB_", 1)[1]
        df_cluster = pd.read_csv(csv_path)
        rows.extend(generate_csv_to_pipeline(df_cluster, df_dm, scenario))

    if not rows:
        raise FileNotFoundError(
            f"Nenhum arquivo de clusterização encontrado com o padrão: "
            f"{DIRETORIO_OUT / padrao}"
        )

    original_row_count = len(rows)
    rows = merge_equal_associations(rows)

    print(f"Gerando arquivo único de pipeline em {output_filename}...")
    with output_filename.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(rows)

    print(
        f"Arquivo gerado com {len(rows)} associações únicas. "
        f"Foram eliminadas {original_row_count - len(rows)} coletas repetidas."
    )
