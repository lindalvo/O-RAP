import sqlite3
import numpy as np
import pandas as pd
from pathlib import Path

from statsmodels.formula.api import ols
from scipy.stats import ttest_rel

from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder
from sklearn.pipeline import Pipeline
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import KFold
from sklearn.metrics import mean_squared_error

DIRETORIO_MAIN = Path(__file__).resolve().parent
DIRETORIO_OUT = (DIRETORIO_MAIN / "../OUT").resolve()

DB_PATH = DIRETORIO_OUT / "metricas.db"

METRICAS = {
    "cpu_usage": "CPU",
    "memory_usage": "Memória",
    "cpu_package_power": "Potência",
    "max_scheduler_latency": "Scheduler",
}


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


# ----------------------------------------------------------------------
# 2. Mesmos tratamentos usados pelo ILP
# ----------------------------------------------------------------------

# Remove leituras inválidas de potência
df = df[
    ~(
        (df["metric"] == "cpu_package_power")
        & (df["value"] > 1000.0)
    )
].copy()

# Corrige overflow de memória para fanout 4 e 5
overflow = (
    (df["metric"] == "memory_usage")
    & (df["num_orus"] >= 4)
    & (df["value"] < 2000.0)
)

df.loc[overflow, "value"] += 4096.0

df["dp_carga_mhz"] = df["dp_carga_mhz"].round(6)


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
# 4. R² parcial
# ----------------------------------------------------------------------

def r2_parcial(modelo_completo, modelo_reduzido):
    """
    Fração da variabilidade residual explicada pela variável retirada.
    """
    return (
        modelo_reduzido.ssr - modelo_completo.ssr
    ) / modelo_reduzido.ssr


# ----------------------------------------------------------------------
# 5. Validação cruzada: assinatura (fanout,carga)
#    versus (fanout,carga,DP)
# ----------------------------------------------------------------------

def rmse_cv(dados_metrica, usar_dp):
    colunas_numericas = ["carga_agregada_mhz"]

    if usar_dp:
        colunas_numericas.append("dp_carga_mhz")

    preprocessador = ColumnTransformer(
        [
            (
                "fanout",
                OneHotEncoder(drop="first"),
                ["num_orus"],
            ),
            (
                "numericas",
                "passthrough",
                colunas_numericas,
            ),
        ]
    )

    modelo = Pipeline(
        [
            ("preprocessador", preprocessador),
            ("regressao", LinearRegression()),
        ]
    )

    colunas = ["num_orus"] + colunas_numericas

    X = dados_metrica[colunas]
    y = dados_metrica["value"].to_numpy()

    kfold = KFold(
        n_splits=10,
        shuffle=True,
        random_state=42,
    )

    rmses = []

    for treino, teste in kfold.split(X):

        modelo.fit(
            X.iloc[treino],
            y[treino],
        )

        previsto = modelo.predict(
            X.iloc[teste]
        )

        rmse = np.sqrt(
            mean_squared_error(
                y[teste],
                previsto,
            )
        )

        rmses.append(rmse)

    return np.array(rmses)


# ----------------------------------------------------------------------
# 6. Análise das quatro métricas
# ----------------------------------------------------------------------

resultados = []

for metric, nome in METRICAS.items():

    d = dados[dados["metric"] == metric].copy()

    # Modelo completo:
    # métrica ~ fanout + carga + DP
    completo = ols(
        "value ~ C(num_orus) + carga_agregada_mhz + dp_carga_mhz",
        data=d,
    ).fit()

    # Remove fanout
    sem_fanout = ols(
        "value ~ carga_agregada_mhz + dp_carga_mhz",
        data=d,
    ).fit()

    # Remove carga agregada
    sem_carga = ols(
        "value ~ C(num_orus) + dp_carga_mhz",
        data=d,
    ).fit()

    # Remove DP
    sem_dp = ols(
        "value ~ C(num_orus) + carga_agregada_mhz",
        data=d,
    ).fit()

    r2_fanout = r2_parcial(
        completo,
        sem_fanout,
    )

    r2_carga = r2_parcial(
        completo,
        sem_carga,
    )

    r2_dp = r2_parcial(
        completo,
        sem_dp,
    )

    # --------------------------------------------------------------
    # Verifica se acrescentar DP melhora a previsão
    # --------------------------------------------------------------

    rmse_sem_dp = rmse_cv(
        d,
        usar_dp=False,
    )

    rmse_com_dp = rmse_cv(
        d,
        usar_dp=True,
    )

    media_sem_dp = rmse_sem_dp.mean()
    media_com_dp = rmse_com_dp.mean()

    ganho_rmse = (
        (media_sem_dp - media_com_dp)
        / media_sem_dp
        * 100
    )

    # H0: incluir DP não reduz o RMSE
    # H1: incluir DP reduz o RMSE
    _, p_dp = ttest_rel(
        rmse_sem_dp,
        rmse_com_dp,
        alternative="greater",
    )

    # DP entra na assinatura somente se
    # melhorar consistentemente o erro preditivo
    if ganho_rmse > 0 and p_dp < 0.05:
        assinatura = "(fanout, carga, DP)"
    else:
        assinatura = "(fanout, carga)"

    resultados.append(
        {
            "Métrica": nome,
            "R2 parcial fanout": r2_fanout,
            "R2 parcial carga": r2_carga,
            "R2 parcial DP": r2_dp,
            "RMSE sem DP": media_sem_dp,
            "RMSE com DP": media_com_dp,
            "Ganho RMSE DP (%)": ganho_rmse,
            "p ganho DP": p_dp,
            "Assinatura indicada": assinatura,
        }
    )


resultado = pd.DataFrame(resultados)

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 200)

print()
print(resultado.round(4).to_string(index=False))