"""Orquestração da campanha com topologias reais da Anatel.

Este módulo contém somente o fluxo principal. As implementações de leitura do
pipeline, banco, YAML, rede, processos e coleta serão adicionadas nos módulos
indicados pelos imports.
"""

from __future__ import annotations

from collections import deque
from random import SystemRandom

from banco import carregar_estado_retomada, chave_topologia
from coleta import coletar_metricas, separar_cenarios
from configuracoes import Topologia, carregar_topologias
from desmontagem import desmontar_topologia
from encerramento import encerrar_processos
from erros import ErroExecucaoRecuperavel
from limpeza import limpar_ambiente_residual
from processos import iniciar_gnb_e_rus
from topologia import montar_topologia
from yamls import gerar_yamls


RODADAS = 10
MAX_TENTATIVAS_POR_TOPOLOGIA = 5
GERADOR_ALEATORIO = SystemRandom()


def _filtrar_pendentes(
    topologias: list[Topologia],
    concluidas: set[tuple[str, str]],
) -> list[Topologia]:
    """Remove topologias cujos cenários individuais já foram coletados."""
    return [
        topologia
        for topologia in topologias
        if not _topologia_concluida(topologia, concluidas)
    ]


def _topologia_concluida(
    topologia: Topologia,
    concluidas: set[tuple[str, str]],
) -> bool:
    """Verifica no banco todos os cenários representados pela topologia."""
    _, identificador = chave_topologia(topologia)
    return all(
        (cenario, identificador) in concluidas
        for cenario in separar_cenarios(topologia.cenario)
    )


def _executar_topologia(topologia: Topologia, roundtrip: int) -> None:
    """Monta, executa e sempre desmonta uma topologia experimental."""
    processos = None
    try:
        arquivos = gerar_yamls(topologia, roundtrip)
        montar_topologia(topologia)
        processos = iniciar_gnb_e_rus(topologia, arquivos)
        coletar_metricas(topologia, roundtrip)
    finally:
        if processos is not None:
            encerrar_processos(processos)
        desmontar_topologia(topologia)


def main() -> None:
    """Executa dez rodadas completas, retomando a rodada interrompida."""
    limpar_ambiente_residual()
    topologias = carregar_topologias()

    if not topologias:
        raise RuntimeError("O arquivo de pipeline não contém topologias")

    maior_roundtrip, concluidas_na_ultima = carregar_estado_retomada()
    rodada_inicial = maior_roundtrip if maior_roundtrip > 0 else 1

    if maior_roundtrip > 0:
        topologias_concluidas = sum(
            _topologia_concluida(topologia, concluidas_na_ultima)
            for topologia in topologias
        )
        print(
            f"Retomada: roundtrip={maior_roundtrip}, "
            f"{topologias_concluidas} de {len(topologias)} "
            "topologia(s) já coletada(s).",
            flush=True,
        )
    else:
        print("Banco sem coletas anteriores; iniciando na rodada 1.", flush=True)

    for roundtrip in range(rodada_inicial, RODADAS + 1):
        concluidas = (
            concluidas_na_ultima
            if roundtrip == maior_roundtrip
            else set()
        )
        pendentes = _filtrar_pendentes(topologias, concluidas)

        if not pendentes:
            print(f"roundtrip={roundtrip} já está completa; ignorando.", flush=True)
            continue

        GERADOR_ALEATORIO.shuffle(pendentes)
        fila = deque(pendentes)
        tentativas = {chave_topologia(item): 0 for item in pendentes}
        esgotadas: list[tuple[Topologia, ErroExecucaoRecuperavel]] = []

        print(
            f"roundtrip={roundtrip}: {len(pendentes)} de {len(topologias)} "
            "topologia(s) pendente(s); ordem aleatória.",
            flush=True,
        )

        while fila:
            topologia = fila.popleft()
            chave = chave_topologia(topologia)
            tentativas[chave] += 1
            tentativa = tentativas[chave]

            print(
                f"roundtrip={roundtrip} "
                f"cenario={topologia.cenario} "
                f"odu={topologia.identificador} "
                f"orus={topologia.quantidade_rus} "
                f"tentativa={tentativa}/{MAX_TENTATIVAS_POR_TOPOLOGIA}",
                flush=True,
            )

            try:
                _executar_topologia(topologia, roundtrip)
            except ErroExecucaoRecuperavel as exc:
                if tentativa < MAX_TENTATIVAS_POR_TOPOLOGIA:
                    # A linha volta ao fim da fila. Assim, as demais topologias
                    # são executadas antes da nova tentativa.
                    fila.append(topologia)
                    print(
                        f"Falha recuperável em {topologia.cenario}/"
                        f"{topologia.identificador}: {exc}. "
                        "Topologia recolocada no fim da fila.",
                        flush=True,
                    )
                else:
                    esgotadas.append((topologia, exc))
                    print(
                        f"Limite de tentativas atingido para "
                        f"{topologia.cenario}/{topologia.identificador}; "
                        "as demais topologias continuarão.",
                        flush=True,
                    )

        if esgotadas:
            falhas = ", ".join(
                f"{item.cenario}/{item.identificador}"
                for item, _ in esgotadas
            )
            raise RuntimeError(
                f"roundtrip={roundtrip} não foi concluída: "
                f"{len(esgotadas)} topologia(s) falharam após "
                f"{MAX_TENTATIVAS_POR_TOPOLOGIA} tentativas: {falhas}. "
                "O pipeline não avançará de rodada; na próxima execução, "
                "o banco identificará e retomará somente as linhas ausentes."
            )

        print(f"roundtrip={roundtrip} concluída.", flush=True)


if __name__ == "__main__":
    main()
