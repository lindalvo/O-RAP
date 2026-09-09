import sqlite3
import numpy as np
import pandas as pd
from pathlib import Path
import statsmodels.formula.api as smf
from common.constantes import clean_metrics_df, DIRETORIO_OUT

DB_PATH = DIRETORIO_OUT / "metricas.db"

METRICAS = {
    "cpu_usage": "CPU",
    "memory_usage": "Memória",
    "cpu_package_power": "Potência",
    "max_scheduler_latency": "Scheduler",
}

def r2_parcial(modelo_completo, modelo_reduzido):
    """
    Fração da variabilidade residual explicada pela variável
    que foi retirada do modelo reduzido.
    """
    return (
        modelo_reduzido.ssr - modelo_completo.ssr
    ) / modelo_reduzido.ssr


# ----------------------------------------------------------------------
# 1. Leitura do banco
# ----------------------------------------------------------------------

with sqlite3.connect(DB_PATH) as con:
    df = pd.read_sql_query(
        """
        SELECT
            num_orus,
            carga_agregada_mhz,
            dp_carga_mhz,
            roundtrip,
            metric,
            value
        FROM stats
        WHERE metric IN (
            'cpu_usage',
            'memory_usage',
            'cpu_package_power',
            'max_scheduler_latency'
        )
        """,
        con,
    )


# Aplica limpeza e correções conhecidas (potência inválida, overflow de memória, arredondamento de DP)
df = clean_metrics_df(df)

# ----------------------------------------------------------------------
# 3. Consolidação das 10 rodadas
# ----------------------------------------------------------------------

# Média das amostras dentro da mesma configuração e rodada
por_rodada = (
    df.groupby(
        [
            "num_orus",
            "carga_agregada_mhz",
            "dp_carga_mhz",
            "roundtrip",
            "metric",
        ],
        as_index=False,
    )["value"]
    .mean()
)

# Média das 10 rodadas
dados = (
    por_rodada.groupby(
        [
            "num_orus",
            "carga_agregada_mhz",
            "dp_carga_mhz",
            "metric",
        ],
        as_index=False,
    )["value"]
    .mean()
)

# ----------------------------------------------------------------------
# Analise
# ----------------------------------------------------------------------

resultados = []

for metric, nome in METRICAS.items():

    d = dados[dados["metric"] == metric].copy()

    # Modelo completo:
    # métrica ~ fanout + carga + DP
    completo = smf.ols(
        "value ~ C(num_orus) + carga_agregada_mhz + dp_carga_mhz",
        data=d,
    ).fit()

    # Remove fanout
    sem_fanout = smf.ols(
        "value ~ carga_agregada_mhz + dp_carga_mhz",
        data=d,
    ).fit()

    # Remove carga agregada
    sem_carga = smf.ols(
        "value ~ C(num_orus) + dp_carga_mhz",
        data=d,
    ).fit()

    # Remove DP
    sem_dp = smf.ols(
        "value ~ C(num_orus) + carga_agregada_mhz",
        data=d,
    ).fit()

    r2_fanout = r2_parcial(completo,sem_fanout)
    r2_carga = r2_parcial(completo,sem_carga)
    r2_dp = r2_parcial(completo,sem_dp)

    # Assinaturas adotadas no ILP
    if metric == "memory_usage":
        assinatura = "(fanout, carga, DP)"
    else:
        assinatura = "(fanout, carga)"

    resultados.append(
        {
            "Métrica": nome,
            "R2 total": completo.rsquared,
            "R2 parcial fanout": r2_fanout,
            "R2 parcial carga": r2_carga,
            "R2 parcial DP": r2_dp,
            "Assinatura ILP": assinatura
        }
    )

resultado = pd.DataFrame(resultados)

# Converte R² para percentual apenas para facilitar a leitura
for coluna in [
    "R2 total",
    "R2 parcial fanout",
    "R2 parcial carga",
    "R2 parcial DP",
]:
    resultado[coluna] *= 100

print()
print(resultado.round(4).to_string(index=False))