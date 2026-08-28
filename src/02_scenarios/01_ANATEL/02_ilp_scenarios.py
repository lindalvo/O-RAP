import math
import os
import sqlite3
import time
from dataclasses import dataclass
from itertools import combinations_with_replacement
from pathlib import Path
from typing import Optional
import pandas as pd
from common.limpeza_metricas import clean_metrics_df
from pyomo.environ import (
    Binary,
    ConcreteModel,
    Constraint,
    Expression,
    NonNegativeReals,
    Objective,
    Param,
    Set,
    Var,
    minimize,
    value,
)
from pyomo.opt import SolverFactory, TerminationCondition

from functions import (
    MAX_CLUSTER_SIZE,
    MAX_FIBER_DISTANCE_KM,
    MAX_LOAD)

DIRETORIO_MAIN = Path(__file__).resolve().parent
DIRETORIO_OUT = (DIRETORIO_MAIN / "../../OUT").resolve()
TIME_LIMIT_SOLVER = int(1800)
MAX_SOLVER_THREADS = int(8)
METRICS_DB_PATH = DIRETORIO_OUT / "metricas.db"

# Métrica empírica otimizada individualmente em cada cenário. As vazões
# não entram no custo porque representam desempenho entregue, e não
# consumo de recursos computacionais.
RESOURCE_OBJECTIVE_METRICS = {
    "mincpu": "cpu_usage",
    "minmemory": "memory_usage",
    "minpower": "cpu_package_power",
    "minmaxsched": "max_scheduler_latency",
}

# Somente a memória apresentou relação consistente com a dispersão das
# cargas individuais depois de controlados o fanout e a carga agregada.
OBJECTIVE_USES_DP = {
    "mincpu": False,
    "minmemory": True,
    "minpower": False,
    "minmaxsched": False,
}

# Larguras de banda contempladas pela campanha atual.
EMPIRICAL_BANDWIDTHS_MHZ = (40, 50, 60, 70, 80, 90, 100)

DP_ROUND_DIGITS = 6

@dataclass(frozen=True)
class DadosModeloILP:
    """Estruturas auxiliares compartilhadas pelas duas etapas lexicográficas."""

    num_estacoes: list[int]
    loads: dict[int, float]
    valid_pairs: list[tuple[int, int]]
    distances: dict[tuple[int, int], float]
    neighbors_by_i: dict[int, list[int]]
    incoming_by_j: dict[int, list[int]]

@dataclass(frozen=True)
class ConfiguracaoEmpirica:
    """Assinatura linearizável e custo de uma configuração de O-DU."""

    config_id: int
    num_orus: int
    carga_agregada_mhz: int
    soma_quadrados_mhz2: Optional[int]
    dp_carga_mhz: Optional[float]
    custo: float
    cargas_compativeis_com_du: frozenset[int]


def carregar_custos_empiricos(
    db_path: Path | str,
    metric: str,
    usar_dp: bool,
) -> pd.DataFrame:
    """
    Consolida o SQLite na granularidade usada pelo objetivo.

    Para memória, o custo é definido por (fanout, carga, desvio-padrão).
    Para CPU, potência e atraso, o custo é definido por (fanout, carga).
    A agregação ocorre primeiro dentro de cada configuração e rodada.
    Nos objetivos sem DP, as configurações de mesmo fanout e carga são
    então promediadas dentro da rodada. Por fim, calcula-se a média entre
    as rodadas, evitando pesos diferentes entre execuções. Também são
    aplicadas as correções conhecidas da campanha:
      * descarte de potência acima de 1000 W;
      * correção do estouro de 32 bits da memória abaixo de 2000 MB.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"Banco de métricas não encontrado: {db_path}")

    if metric not in set(RESOURCE_OBJECTIVE_METRICS.values()):
        raise ValueError(f"Métrica empírica não suportada: {metric!r}.")

    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
        stats = pd.read_sql_query(
            """
            SELECT num_orus, carga_agregada_mhz, dp_carga_mhz,
                   roundtrip, metric, value
            FROM stats
            WHERE metric = ?
            """,
            connection,
            params=(metric,),
        )

    required_columns = {
        "num_orus", "carga_agregada_mhz", "dp_carga_mhz",
        "roundtrip", "metric", "value",
    }
    if not required_columns.issubset(stats.columns):
        missing = sorted(required_columns - set(stats.columns))
        raise ValueError(f"Colunas ausentes na tabela stats: {missing}")

    # Aplica correções e normalizações conhecidas nas métricas
    stats = clean_metrics_df(
        stats,
        dp_round_digits=DP_ROUND_DIGITS,
        create_dp_key=True,
    )

    full_key_columns = ["num_orus", "carga_agregada_mhz", "dp_key"]
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
        .pivot(index=key_columns, columns="metric", values="value")
        .reset_index()
    )

    if metric not in consolidated.columns:
        raise ValueError(f"O banco não contém a métrica {metric!r}.")

    consolidated = consolidated.dropna(subset=[metric]).copy()
    consolidated["custo_empirico"] = consolidated[metric].astype(float)
    consolidated["num_orus"] = consolidated["num_orus"].astype(int)
    consolidated["carga_agregada_mhz"] = (
        consolidated["carga_agregada_mhz"].astype(int)
    )
    return consolidated.sort_values(key_columns).reset_index(drop=True)

def enumerar_clusters_viaveis(
    custos_observados: pd.DataFrame,
    usar_dp: bool,
) -> dict[int, ConfiguracaoEmpirica]:
    """
    Enumera assinaturas viáveis de configuração, não subconjuntos de RUs.

    Para memória, cada assinatura guarda fanout, soma das cargas e soma dos
    quadrados, permitindo impor o desvio-padrão por restrições lineares.
    Para os demais objetivos, a assinatura contém apenas fanout e carga.
    """
    if usar_dp:
        observed_by_key = {
            (
                int(row.num_orus),
                int(row.carga_agregada_mhz),
                round(float(row.dp_key), DP_ROUND_DIGITS),
            ): float(row.custo_empirico)
            for row in custos_observados.itertuples(index=False)
        }
    else:
        observed_by_key = {
            (
                int(row.num_orus),
                int(row.carga_agregada_mhz),
            ): float(row.custo_empirico)
            for row in custos_observados.itertuples(index=False)
        }

    signatures: dict[tuple[int, ...], dict[str, object]] = {}
    for num_orus in range(1, MAX_CLUSTER_SIZE + 1):
        for loads in combinations_with_replacement(
            EMPIRICAL_BANDWIDTHS_MHZ,
            num_orus,
        ):
            total_load = int(sum(loads))
            if total_load > MAX_LOAD:
                continue
            sum_squares = int(sum(load * load for load in loads))
            mean_load = total_load / num_orus
            dp_load = math.sqrt(
                sum((load - mean_load) ** 2 for load in loads) / num_orus
            )
            signature_key = (
                (num_orus, total_load, sum_squares)
                if usar_dp
                else (num_orus, total_load)
            )
            item = signatures.setdefault(
                signature_key,
                {
                    "dp": round(dp_load, DP_ROUND_DIGITS),
                    "self_loads": set(),
                },
            )
            item["self_loads"].update(loads)

    configurations: dict[int, ConfiguracaoEmpirica] = {}
    for config_id, (signature_key, item) in enumerate(
        sorted(signatures.items()),
        start=1,
    ):
        num_orus = int(signature_key[0])
        total_load = int(signature_key[1])
        sum_squares = int(signature_key[2]) if usar_dp else None
        dp_load = float(item["dp"]) if usar_dp else None
        observed_key = (
            (num_orus, total_load, dp_load)
            if usar_dp
            else (num_orus, total_load)
        )
        observed_cost = observed_by_key.get(observed_key)
        if observed_cost is None:
            raise ValueError(
                "Configuração empírica ausente no banco completo: "
                f"{observed_key}."
            )
        configurations[config_id] = ConfiguracaoEmpirica(
            config_id=config_id,
            num_orus=num_orus,
            carga_agregada_mhz=total_load,
            soma_quadrados_mhz2=sum_squares,
            dp_carga_mhz=dp_load,
            custo=float(observed_cost),
            cargas_compativeis_com_du=frozenset(item["self_loads"]),
        )

    return configurations

def preparar_dados_modelo(
    df: pd.DataFrame,
    df_dm: pd.DataFrame,
) -> DadosModeloILP:
    """Prepara cargas, pares RU-DU viáveis e listas de adjacência."""
    num_estacoes = df["NumEstacao"].astype(int).tolist()
    id_set = set(num_estacoes)
    loads = (
        df.assign(NumEstacao=df["NumEstacao"].astype(int))
        .set_index("NumEstacao")["bandwidth"]
        .astype(float)
        .to_dict()
    )

    valid_pairs: list[tuple[int, int]] = []
    distances: dict[tuple[int, int], float] = {}
    neighbors_by_i = {i: [] for i in num_estacoes}
    incoming_by_j = {j: [] for j in num_estacoes}

    for i in num_estacoes:
        if i not in df_dm.index:
            raise KeyError(f"A estação {i} não existe no índice da matriz de distâncias.")

        dists_i = df_dm.loc[i]
        valid_neighbors = [
            int(j)
            for j in dists_i[dists_i <= MAX_FIBER_DISTANCE_KM].index
            if int(j) in id_set
        ]

        if not valid_neighbors:
            raise ValueError(
                f"A RU {i} não possui nenhuma DU viável dentro de "
                f"{MAX_FIBER_DISTANCE_KM} km."
            )

        for j in valid_neighbors:
            pair = (i, j)
            valid_pairs.append(pair)
            distances[pair] = float(dists_i[j])
            neighbors_by_i[i].append(j)
            incoming_by_j[j].append(i)

    return DadosModeloILP(
        num_estacoes=num_estacoes,
        loads=loads,
        valid_pairs=valid_pairs,
        distances=distances,
        neighbors_by_i=neighbors_by_i,
        incoming_by_j=incoming_by_j,
    )


def criar_modelo_base(
    df: pd.DataFrame,
    df_dm: pd.DataFrame,
    nome_modelo: str,
) -> tuple[ConcreteModel, DadosModeloILP]:
    """
    Cria a parte comum dos modelos primário e secundário.

    Inclui conjuntos, parâmetros, variáveis e as restrições de associação,
    capacidade, autoassociação e cardinalidade. As restrições e os objetivos
    específicos de cada etapa são adicionados pelas funções chamadoras.
    """
    dados = preparar_dados_modelo(df, df_dm)

    print(f"Iniciando modelagem ILP para {len(dados.num_estacoes)} estações...")
    print(
        "Critério de distância máxima para pares RU-DU: "
        f"{MAX_FIBER_DISTANCE_KM} km"
    )
    print(f"Critério de carga máxima por DU: {MAX_LOAD} MHz")
    print(
        "Critério de tamanho máximo de cluster: "
        f"{MAX_CLUSTER_SIZE} RUs (incluindo a própria DU)"
    )
    print(
        "Número de pares válidos (i,j) para conexão: "
        f"{len(dados.valid_pairs)}"
    )

    modelo = ConcreteModel(nome_modelo)

    modelo.I = Set(initialize=dados.num_estacoes, ordered=True)
    modelo.A = Set(dimen=2, initialize=dados.valid_pairs, ordered=False)

    modelo.ru_load = Param(
        modelo.I,
        initialize=lambda mm, i: float(dados.loads[i]),
        within=NonNegativeReals,
        mutable=False,
    )
    modelo.dist = Param(
        modelo.A,
        initialize=lambda mm, i, j: float(dados.distances[(i, j)]),
        within=NonNegativeReals,
        mutable=False,
    )

    # y[j] = 1 se a estação j for ativada como O-DU.
    modelo.y = Var(modelo.I, domain=Binary)
    # x[i,j] = 1 se a O-RU i for atendida pela O-DU j.
    modelo.x = Var(modelo.A, domain=Binary)

    def assign_rule(mm, i):
        return sum(mm.x[i, j] for j in dados.neighbors_by_i[i]) == 1

    modelo.Assign = Constraint(modelo.I, rule=assign_rule)

    def capacity_rule(mm, j):
        return (
            sum(
                mm.ru_load[i] * mm.x[i, j]
                for i in dados.incoming_by_j[j]
            )
            <= float(MAX_LOAD) * mm.y[j]
        )

    modelo.Capacity = Constraint(modelo.I, rule=capacity_rule)

    def self_assignment_rule(mm, j):
        return mm.x[j, j] == mm.y[j]

    modelo.SelfAssignment = Constraint(modelo.I, rule=self_assignment_rule)

    def max_cluster_size_rule(mm, j):
        return (
            sum(mm.x[i, j] for i in dados.incoming_by_j[j])
            <= MAX_CLUSTER_SIZE * mm.y[j]
        )

    modelo.MaxClusterSize = Constraint(
        modelo.I,
        rule=max_cluster_size_rule,
    )

    TAXA_MAX_OCUPACAO_REDE = 0.90
    def global_redundancy_rule(mm):
        total_orus = len(dados.num_estacoes)
        return total_orus <= (MAX_CLUSTER_SIZE * TAXA_MAX_OCUPACAO_REDE) * sum(mm.y[j] for j in mm.I)

    modelo.GlobalRedundancy = Constraint(rule=global_redundancy_rule)

    return modelo, dados


def aplicar_valores_iniciais(
    modelo: ConcreteModel,
    dados: DadosModeloILP,
    assignment: dict[int, int],
) -> None:
    """Define os valores iniciais de x e y para o warm start."""
    dus = set(assignment.values())

    for j in dados.num_estacoes:
        modelo.y[j].value = 1 if j in dus else 0

    for i, j in dados.valid_pairs:
        modelo.x[i, j].value = 1 if assignment.get(i) == j else 0


def extrair_atribuicao(
    modelo: ConcreteModel,
    dados: DadosModeloILP,
) -> dict[int, int]:
    """Extrai do modelo resolvido o mapeamento O-RU -> O-DU."""
    assignment: dict[int, int] = {}

    for i in dados.num_estacoes:
        chosen = None
        for j in dados.neighbors_by_i[i]:
            x_value = value(modelo.x[i, j])
            if x_value is not None and x_value > 0.5:
                chosen = int(j)
                break

        if chosen is None:
            raise RuntimeError(
                f"Não foi possível extrair uma atribuição viável para a RU {i}."
            )

        assignment[i] = chosen

    return assignment


def criar_solver(
    etapa: str,
    objective_mode: Optional[str] = None,
):
    """
    Na etapa primária, prioriza o fechamento do gap e utiliza gap absoluto
    inferior a uma unidade, adequado ao objetivo inteiro de quantidade de
    O-DUs. Na etapa secundária, mantém configuração distinta para o MILP
    minlink e para os quatro objetivos baseados nas métricas empíricas.
    """
    threads = min(os.cpu_count() or 4, MAX_SOLVER_THREADS)
    opt = SolverFactory("gurobi")

    if not opt.available(exception_flag=False):
        raise RuntimeError(
            "O solver Gurobi não está disponível. Verifique a instalação, "
            "o PATH e a licença acadêmica."
        )

    opt.options.clear()
    opt.options["Threads"] = int(threads)
    opt.options["TimeLimit"] = float(TIME_LIMIT_SOLVER)

    if etapa == "primaria":
        # Aceita gap absoluto de 1 unidade, pois o objetivo é inteiro (quantidade de O-DUs). O gap relativo é zero, garantindo que a solução ótima seja provada.
        opt.options["MIPGapAbs"] = 0.999
        opt.options["MIPGap"] = 0.001
        # Aceita solução interia imediatamente acima do limite inferior, mesmo que não seja ótima, para acelerar a convergência.
        #opt.options["MIPGapAbs"] = 1.001
        #opt.options["MIPGap"] = 0.0

        opt.options["MIPFocus"] = 2

    elif etapa == "secundaria":
        if objective_mode == "minlink":
            # MILP linear: minimização da soma das distâncias.
            opt.options["MIPGap"] = 0.001

        elif objective_mode in RESOURCE_OBJECTIVE_METRICS:
            opt.options["MIPGap"] = 0.001
            opt.options["MIPFocus"] = 1
        else:
            raise ValueError(
                "objective_mode deve ser 'minlink', 'mincpu', "
                "'minmemory', 'minpower' ou 'minmaxsched'; "
                f"recebido: {objective_mode!r}."
            )
    else:
        raise ValueError(f"Etapa de solução desconhecida: {etapa!r}.")

    return opt, threads


def cluster_ilp_primario(
    df: pd.DataFrame,
    df_dm: pd.DataFrame,
) -> tuple[int, dict[int, int]]:
    """
    Executa a primeira etapa lexicográfica.

    O objetivo é encontrar e provar a menor quantidade viável de O-DUs.
    """
    modelo, dados = criar_modelo_base(
        df,
        df_dm,
        nome_modelo="O-RAP_Primario",
    )

    lower_bound_fanout = math.ceil(
        len(dados.num_estacoes) / MAX_CLUSTER_SIZE
    )
    lower_bound_capacity = math.ceil(
        sum(dados.loads.values()) / MAX_LOAD
    )
    lower_bound_du = max(lower_bound_fanout, lower_bound_capacity)

    modelo.DULowerBound = Constraint(
        expr=sum(modelo.y[j] for j in modelo.I) >= lower_bound_du
    )

    print(
        "Limite inferior simples para o número de O-DUs: "
        f"{lower_bound_du} "
        f"(fanout={lower_bound_fanout}, "
        f"capacidade={lower_bound_capacity})."
    )

    modelo.Stage1OBJ = Objective(
        expr=sum(modelo.y[j] for j in modelo.I),
        sense=minimize,
    )

    opt, threads = criar_solver(etapa="primaria")

    print(
        "Minimizando o número de DUs com solver "
        f"gurobi (threads={threads})..."
    )
    print(f"Tempo limite {TIME_LIMIT_SOLVER} segundos...")

    inicio = time.perf_counter()
    results = opt.solve(modelo, tee=True)
    fim = time.perf_counter()

    print(f"Tempo de resolução primeira etapa: {fim - inicio:.2f} segundos.")
    term = results.solver.termination_condition
    print(f"TerminationCondition: {term}.")

    if term != TerminationCondition.optimal:
        raise RuntimeError(
            "A minimização do número de DUs não provou otimalidade. "
            f"TerminationCondition: {term}. Para garantir o número mínimo "
            "de DUs, a primeira etapa precisa ser ótima."
        )

    best_du_count = int(
        round(sum(value(modelo.y[j]) for j in modelo.I))
    )
    print(f"Melhor quantidade de O-DUs encontrada dentro da tolerância configurada: {best_du_count}")

    optimal_assignment = extrair_atribuicao(modelo, dados)
    return best_du_count, optimal_assignment


def cluster_ilp_secundario(
    df: pd.DataFrame,
    df_dm: pd.DataFrame,
    best_du_count: int,
    objective_mode: str,
    initial_assignment: Optional[dict[int, int]] = None,
) -> pd.DataFrame:
    """
    Executa a segunda etapa lexicográfica com o número de O-DUs fixado.

    Modos:
      - ``minlink``: minimiza a soma total das distâncias O-RU--O-DU;
      - ``mincpu``: minimiza a CPU total prevista;
      - ``minmemory``: minimiza a memória total prevista;
      - ``minpower``: minimiza a potência total prevista;
      - ``minmaxsched``: minimiza a maior latência máxima prevista.

    Nos quatro modos empíricos, as localizações das O-DUs são fixadas pelo
    cenário minlink e a menor distância total é usada como desempate.
    """
    modelo, dados = criar_modelo_base(
        df,
        df_dm,
        nome_modelo=f"O-RAP_Secundario_{objective_mode}",
    )

    modelo.FixDUCount = Constraint(
        expr=sum(modelo.y[j] for j in modelo.I) == best_du_count
    )

    # Expressão compartilhada: carga agregada atendida pela candidata j.
    def du_load_rule(mm, j):
        return sum(
            mm.ru_load[i] * mm.x[i, j]
            for i in dados.incoming_by_j[j]
        )

    modelo.DULoad = Expression(modelo.I, rule=du_load_rule)

    resource_configurations: Optional[dict[int, ConfiguracaoEmpirica]] = None

    if objective_mode == "minlink":
        msg = "Minimizando a soma total das distâncias RU-DU."

        modelo.Stage2OBJ = Objective(
            expr=sum(
                modelo.dist[i, j] * modelo.x[i, j]
                for i, j in modelo.A
            ),
            sense=minimize,
        )

    elif objective_mode in RESOURCE_OBJECTIVE_METRICS:
        if initial_assignment is None:
            raise ValueError(
                f"{objective_mode} exige a atribuição do cenário minlink "
                "para fixar as localizações das O-DUs."
            )

        optimized_dus = {int(du) for du in initial_assignment.values()}

        def fix_optimized_dus_rule(mm, j):
            return mm.y[j] == (1 if j in optimized_dus else 0)

        modelo.FixOptimizedDUs = Constraint(
            modelo.I,
            rule=fix_optimized_dus_rule,
        )

        modelo.OptimizedDUs = Set(
            initialize=sorted(optimized_dus),
            ordered=True,
        )

        target_metric = RESOURCE_OBJECTIVE_METRICS[objective_mode]
        usar_dp = OBJECTIVE_USES_DP[objective_mode]
        custos_observados = carregar_custos_empiricos(
            METRICS_DB_PATH,
            target_metric,
            usar_dp=usar_dp,
        )
        resource_configurations = enumerar_clusters_viaveis(
            custos_observados,
            usar_dp=usar_dp,
        )

        # Uma configuração só é disponibilizada para a O-DU j se ao
        # menos uma combinação que gera sua assinatura contém a largura de
        # banda da própria O-DU, cuja autoassociação é obrigatória.
        resource_index = [
            (j, config_id)
            for j in sorted(optimized_dus)
            for config_id, config in resource_configurations.items()
            if int(round(dados.loads[j]))
            in config.cargas_compativeis_com_du
        ]
        modelo.ResourceConfigIndex = Set(
            dimen=2,
            initialize=resource_index,
            ordered=False,
        )
        modelo.q = Var(modelo.ResourceConfigIndex, domain=Binary)

        configs_by_du = {
            j: [
                config_id
                for jj, config_id in resource_index
                if jj == j
            ]
            for j in optimized_dus
        }

        modelo.SelectOneResourceConfiguration = Constraint(
            modelo.OptimizedDUs,
            rule=lambda mm, j: sum(
                mm.q[j, config_id]
                for config_id in configs_by_du[j]
            )
            == 1,
        )

        modelo.LinkResourceFanout = Constraint(
            modelo.OptimizedDUs,
            rule=lambda mm, j: sum(
                mm.x[i, j] for i in dados.incoming_by_j[j]
            )
            == sum(
                resource_configurations[config_id].num_orus
                * mm.q[j, config_id]
                for config_id in configs_by_du[j]
            ),
        )
        modelo.LinkResourceLoad = Constraint(
            modelo.OptimizedDUs,
            rule=lambda mm, j: mm.DULoad[j]
            == sum(
                resource_configurations[config_id].carga_agregada_mhz
                * mm.q[j, config_id]
                for config_id in configs_by_du[j]
            ),
        )
        if usar_dp:
            modelo.DUSquaredLoad = Expression(
                modelo.OptimizedDUs,
                rule=lambda mm, j: sum(
                    float(dados.loads[i]) ** 2 * mm.x[i, j]
                    for i in dados.incoming_by_j[j]
                ),
            )
            modelo.LinkResourceSquaredLoad = Constraint(
                modelo.OptimizedDUs,
                rule=lambda mm, j: mm.DUSquaredLoad[j]
                == sum(
                    resource_configurations[
                        config_id
                    ].soma_quadrados_mhz2
                    * mm.q[j, config_id]
                    for config_id in configs_by_du[j]
                ),
            )

        modelo.EmpiricalMetricTotal = Expression(
            expr=sum(
                resource_configurations[config_id].custo
                * modelo.q[j, config_id]
                for j, config_id in modelo.ResourceConfigIndex
            )
        )
        modelo.TotalDistance = Expression(
            expr=sum(
                modelo.dist[i, j] * modelo.x[i, j]
                for i, j in modelo.A
            )
        )
        if objective_mode == "minmaxsched":
            modelo.MaxPredictedSchedulerLatency = Var(
                domain=NonNegativeReals
            )
            modelo.BoundPredictedSchedulerLatency = Constraint(
                modelo.OptimizedDUs,
                rule=lambda mm, j: mm.MaxPredictedSchedulerLatency
                >= sum(
                    resource_configurations[config_id].custo
                    * mm.q[j, config_id]
                    for config_id in configs_by_du[j]
                ),
            )
            modelo.EmpiricalPrimaryObjective = Expression(
                expr=modelo.MaxPredictedSchedulerLatency
            )
        else:
            modelo.EmpiricalPrimaryObjective = Expression(
                expr=modelo.EmpiricalMetricTotal
            )

        modelo.Stage2OBJ = Objective(
            expr=modelo.EmpiricalPrimaryObjective,
            sense=minimize,
        )

        print(
            "Assinaturas empíricas observadas: "
            f"{len(resource_configurations)}."
        )
        print(
            "Granularidade da assinatura: "
            + (
                "fanout, carga agregada e desvio-padrão."
                if usar_dp
                else "fanout e carga agregada."
            )
        )
        print(
            "Variáveis O-DU/configuração criadas: "
            f"{len(resource_index)}."
        )
        metric_messages = {
            "mincpu": "Minimizando a CPU total prevista.",
            "minmemory": "Minimizando a memória total prevista.",
            "minpower": "Minimizando a potência total prevista.",
            "minmaxsched": "Minimizando a maior latência máxima prevista.",
        }
        msg = metric_messages[objective_mode]

    else:
        raise ValueError(
            "objective_mode deve ser 'minlink', 'mincpu', "
            "'minmemory', 'minpower' ou 'minmaxsched'; "
            f"recebido: {objective_mode!r}."
        )

    if initial_assignment is not None:
        aplicar_valores_iniciais(modelo, dados, initial_assignment)

    ok_terms = {
        TerminationCondition.optimal,
        TerminationCondition.maxTimeLimit,
        TerminationCondition.feasible,
    }

    print(f"{msg}")
    print(f"Tempo limite por passagem: {TIME_LIMIT_SOLVER} segundos...")

    opt, threads = criar_solver(
        etapa="secundaria",
        objective_mode=objective_mode,
    )

    print(f"{msg} com solver gurobi (threads={threads})...")
    print(f"Tempo limite {TIME_LIMIT_SOLVER} segundos...")

    if objective_mode == "minlink":
        opt, threads = criar_solver(
            etapa="secundaria",
            objective_mode="minlink",
        )

        print(f"Solver gurobi (threads={threads})...")

        inicio = time.perf_counter()
        results = opt.solve(
            modelo,
            tee=True,
            warmstart=initial_assignment is not None,
        )
        fim = time.perf_counter()

        term = results.solver.termination_condition
        print(
            f"Tempo de resolução segunda etapa: "
            f"{fim - inicio:.2f} segundos."
        )
        print(f"TerminationCondition: {term}.")

        if term not in ok_terms:
            raise RuntimeError(
                f"Gurobi terminou em estado não aceito: {term}."
            )

    else:
        # Passagem 1: minimização do objetivo empírico do cenário.
        opt_resource, threads_resource = criar_solver(
            etapa="secundaria",
            objective_mode=objective_mode,
        )

        print(
            f"\n--- Passagem {objective_mode} 1/2: "
            "minimização da métrica empírica ---"
        )
        print(f"Solver gurobi (threads={threads_resource})...")

        inicio_resource = time.perf_counter()
        results_resource = opt_resource.solve(
            modelo,
            tee=True,
            warmstart=True,
        )
        fim_resource = time.perf_counter()

        term_resource = results_resource.solver.termination_condition
        print(
            f"Tempo da passagem {objective_mode} 1: "
            f"{fim_resource - inicio_resource:.2f} segundos."
        )
        print(
            f"TerminationCondition {objective_mode} 1: "
            f"{term_resource}."
        )
        if term_resource not in ok_terms:
            raise RuntimeError(
                "Gurobi terminou a minimização do custo em estado "
                f"não aceito: {term_resource}."
            )

        best_empirical_objective = float(
            value(modelo.EmpiricalPrimaryObjective)
        )
        resource_assignment = extrair_atribuicao(modelo, dados)
        aplicar_valores_iniciais(modelo, dados, resource_assignment)

        modelo.Stage2OBJ.deactivate()
        cost_tolerance = max(
            1e-6,
            abs(best_empirical_objective) * 1e-6,
        )
        modelo.KeepBestEmpiricalObjective = Constraint(
            expr=modelo.EmpiricalPrimaryObjective
            <= best_empirical_objective + cost_tolerance
        )
        modelo.DistanceOBJ = Objective(
            expr=modelo.TotalDistance,
            sense=minimize,
        )

        print(
            f"\n--- Passagem {objective_mode} 2/2: "
            "minimização da distância como desempate ---"
        )
        print(
            "Melhor valor do objetivo empírico preservado: "
            f"{best_empirical_objective:.8f}."
        )

        opt_distance, threads_distance = criar_solver(
            etapa="secundaria",
            objective_mode="minlink",
        )
        inicio_distance = time.perf_counter()
        results_distance = opt_distance.solve(
            modelo,
            tee=True,
            warmstart=True,
        )
        fim_distance = time.perf_counter()
        term_distance = results_distance.solver.termination_condition
        print(
            f"Tempo da passagem {objective_mode} 2: "
            f"{fim_distance - inicio_distance:.2f} segundos."
        )
        print(
            f"TerminationCondition {objective_mode} 2: "
            f"{term_distance}."
        )
        if term_distance not in ok_terms:
            raise RuntimeError(
                "Gurobi terminou o desempate por distância em estado "
                f"não aceito: {term_distance}."
            )

        selected_configs = []
        for j, config_id in modelo.ResourceConfigIndex:
            q_value = value(modelo.q[j, config_id])
            if q_value is not None and q_value > 0.5:
                config = resource_configurations[config_id]
                selected_configs.append((int(j), config))

        print(
            "Valor final do objetivo empírico: "
            f"{value(modelo.EmpiricalPrimaryObjective):.8f}."
        )
        print(
            "Distância total final: "
            f"{value(modelo.TotalDistance):.6f} km."
        )
        for j, config in sorted(selected_configs):
            description = (
                f"  O-DU {j}: n={config.num_orus}, "
                f"carga={config.carga_agregada_mhz} MHz"
            )
            if config.dp_carga_mhz is not None:
                description += (
                    f", dp={config.dp_carga_mhz:.6f} MHz"
                )
            description += f", custo={config.custo:.8f} (observado)"
            print(description)

    # Em maxTimeLimit, a extração também funciona como verificação de que existe um incumbente inteiro carregado no modelo.
    assignment = extrair_atribuicao(modelo, dados)

    active_dus = sorted(set(assignment.values()))
    if len(active_dus) != best_du_count:
        raise RuntimeError(
            "A solução extraída não respeita a quantidade fixa de O-DUs: "
            f"{len(active_dus)} != {best_du_count}."
        )

    if objective_mode in RESOURCE_OBJECTIVE_METRICS:
        du_loads = {
            j: sum(
                dados.loads[i]
                for i, assigned_j in assignment.items()
                if assigned_j == j
            )
            for j in active_dus
        }

        mean_load = sum(du_loads.values()) / len(du_loads)
        variance = sum(
            (load - mean_load) ** 2
            for load in du_loads.values()
        ) / len(du_loads)
        standard_deviation = math.sqrt(variance)

        print(
            "Carga agregada das O-DUs: "
            f"mínima={min(du_loads.values()):.2f} MHz; "
            f"máxima={max(du_loads.values()):.2f} MHz; "
            f"média={mean_load:.2f} MHz; "
            f"desvio padrão={standard_deviation:.2f} MHz."
        )

    result_df = df.copy()
    result_df["O-DU"] = (
        result_df["NumEstacao"].astype(int).map(assignment)
    )

    if result_df["O-DU"].isna().any():
        missing_rows = result_df.loc[
            result_df["O-DU"].isna(),
            "NumEstacao",
        ].tolist()
        raise RuntimeError(
            "Algumas O-RUs não receberam O-DU no resultado: "
            f"{missing_rows[:10]}."
        )

    result_df["O-DU"] = result_df["O-DU"].astype(int)
    return result_df

if __name__ == "__main__":
    # Abre os dados de entrada das O-RUs candidatas à clusterização.
    csv_path = DIRETORIO_OUT / f"grp_RMB.csv"
    print(f"Carregando o arquivo {csv_path}")
    df = pd.read_csv(csv_path)

    df.drop(
        columns=[
            "N_Latitude",
            "N_Longitude",
            "Latitudes",
            "Longitudes",
            "N_Designacoes",
            "Designacoes",
            "N_Setores",
            "Setores",
        ],
        inplace=True,
    )
    df["NumEstacao"] = df["NumEstacao"].astype(int)

    csv_path = DIRETORIO_OUT / f"dm_RMB.csv"
    if not csv_path.exists():
        raise FileNotFoundError(
            f"Matriz de distâncias não encontrada: {csv_path}"
        )

    df_dm = pd.read_csv(csv_path, index_col="NumEstacao")
    df_dm.index = df_dm.index.astype(int)
    df_dm.columns = df_dm.columns.astype(int)

    best_du_count, primary_assignment = cluster_ilp_primario(df, df_dm)

    # Cenário minlink: menor soma das distâncias RU-DU.
    df_minlink = cluster_ilp_secundario(df,df_dm,best_du_count,objective_mode="minlink",initial_assignment=primary_assignment,)
    optimized_output = DIRETORIO_OUT / f"ilp_RMB_minlink.csv"
    print(f"Gravando o cenário minlink em {optimized_output}")
    df_minlink.to_csv(optimized_output, index=False)
    #df_minlink = pd.read_csv(optimized_output)
    
    minlink_assignment = (
        df_minlink.assign(
            NumEstacao=df_minlink["NumEstacao"].astype(int),
            **{"O-DU": df_minlink["O-DU"].astype(int)},
        )
        .set_index("NumEstacao")["O-DU"]
        .to_dict()
    )

    # Cenários empíricos com as posições de O-DU fixadas pelo minlink.
    for objective_mode in RESOURCE_OBJECTIVE_METRICS:
        df_scenario = cluster_ilp_secundario(
            df,
            df_dm,
            best_du_count,
            objective_mode=objective_mode,
            initial_assignment=minlink_assignment,
        )
        scenario_output = (
            DIRETORIO_OUT / f"ilp_RMB_{objective_mode}.csv"
        )
        print(
            f"Gravando o cenário {objective_mode} em {scenario_output}"
        )
        df_scenario.to_csv(scenario_output, index=False)
