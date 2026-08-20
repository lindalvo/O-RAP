import os
from pathlib import Path
import pandas as pd

DIRETORIO_MAIN = Path(__file__).resolve().parent
DIRETORIO_OUT = (DIRETORIO_MAIN / "../OUT").resolve()


def gerar_tabela_auxiliar_odus() -> pd.DataFrame:
    """
    Verifica os arquivos ILP e Greedy dos cenários minlink e resource_aware,
    confirma que todos possuem exatamente o mesmo conjunto de O-DUs e gera
    uma tabela auxiliar única com identificadores sequenciais.

    Arquivos esperados:
        ilp_RMB_minlink.csv
        ilp_RMB_resource_aware.csv
        grd_RMB_resource_aware.csv

    Saída:
        association_odus_{filename}.csv

    Colunas:
        O-DU_ID
        O-DU
    """

    arquivos_encontrados: list[dict] = []

    for prefixo in ("ilp_", "grd_"):
        padrao = f"{prefixo}RMB_*.csv"

        for arquivo_csv in sorted(DIRETORIO_OUT.glob(padrao)):
            cadeia = arquivo_csv.stem.split(
                f"{prefixo}RMB_",
                1,
            )[1]

            algoritmo = prefixo.rstrip("_")
            scenario = cadeia

            arquivos_encontrados.append(
                {
                    "arquivo": arquivo_csv,
                    "algoritmo": algoritmo,
                    "scenario": scenario,
                }
            )

    if not arquivos_encontrados:
        raise FileNotFoundError(
            f"Nenhum arquivo encontrado em {DIRETORIO_OUT} para os padrões "
            f"'ilp_RMB_*.csv' e 'grd_RMB_*.csv'."
        )

    conjunto_referencia: set[int] | None = None
    arquivo_referencia: Path | None = None

    for item in arquivos_encontrados:
        arquivo_csv = item["arquivo"]
        algoritmo = item["algoritmo"]
        scenario = item["scenario"]

        print(
            f"Carregando {arquivo_csv.name} "
            f"[algoritmo={algoritmo}, cenário={scenario}]"
        )

        clusters = pd.read_csv(
            arquivo_csv,
            dtype={"O-DU": "Int64"},
        )

        if "O-DU" not in clusters.columns:
            raise ValueError(
                f"O arquivo {arquivo_csv.name} não possui a coluna 'O-DU'."
            )

        odus_arquivo = set(
            clusters["O-DU"]
            .dropna()
            .astype(int)
            .unique()
            .tolist()
        )

        if conjunto_referencia is None:
            conjunto_referencia = odus_arquivo
            arquivo_referencia = arquivo_csv
            continue

        if odus_arquivo != conjunto_referencia:
            odus_faltantes = sorted(conjunto_referencia - odus_arquivo)
            odus_adicionais = sorted(odus_arquivo - conjunto_referencia)

            mensagem = [
                "Os conjuntos de O-DUs não são iguais.",
                f"Arquivo de referência: {arquivo_referencia.name}",
                f"Arquivo divergente: {arquivo_csv.name}",
                (
                    f"Quantidade no arquivo de referência: "
                    f"{len(conjunto_referencia)}"
                ),
                (
                    f"Quantidade no arquivo divergente: "
                    f"{len(odus_arquivo)}"
                ),
            ]

            if odus_faltantes:
                mensagem.append(
                    f"O-DUs ausentes no arquivo divergente: {odus_faltantes}"
                )

            if odus_adicionais:
                mensagem.append(
                    f"O-DUs adicionais no arquivo divergente: {odus_adicionais}"
                )

            raise ValueError("\n".join(mensagem))

    # Ordenação numérica para que a associação seja determinística e
    # independente da ordem das linhas e da ordem de leitura dos arquivos.
    odus_ordenadas = sorted(conjunto_referencia)

    tabela_odus = pd.DataFrame(
        {
            "O-DU_ID": range(1, len(odus_ordenadas) + 1),
            "O-DU": odus_ordenadas,
        }
    )

    arquivo_saida = DIRETORIO_OUT / f"ta_RMB.csv"

    tabela_odus.to_csv(
        arquivo_saida,
        index=False,
        encoding="utf-8",
    )

    print()
    print(
        f"Validação concluída: os {len(arquivos_encontrados)} arquivos "
        f"possuem as mesmas {len(tabela_odus)} O-DUs."
    )
    print(f"Tabela auxiliar gravada em: {arquivo_saida}")

    return tabela_odus


if __name__ == "__main__":
    tabela_auxiliar = gerar_tabela_auxiliar_odus()
