"""Laço principal do pipeline experimental."""

from random import SystemRandom

from banco import carregar_estado_retomada, chave_configuracao
from coleta import coletar_metricas
from configuracoes import gerar_configuracoes
from desmontagem import desmontar_topologia
from encerramento import encerrar_processos
from limpeza import limpar_ambiente_residual
from processos import iniciar_gnb_e_rus
from topologia import montar_topologia
from yamls import gerar_yamls


RODADAS = 10
GERADOR_ALEATORIO = SystemRandom()


def main() -> None:
    limpar_ambiente_residual()
    configuracoes = gerar_configuracoes()
    maior_roundtrip, presentes_na_ultima = carregar_estado_retomada()
    rodada_inicial = maior_roundtrip if maior_roundtrip > 0 else 1

    if maior_roundtrip > 0:
        print(
            f"Retomada: roundtrip={maior_roundtrip}, "
            f"{len(presentes_na_ultima)} de {len(configuracoes)} "
            "configuração(ões) já coletada(s).",
            flush=True,
        )
    else:
        print("Banco sem coletas anteriores; iniciando na rodada 1.")

    for roundtrip in range(rodada_inicial, RODADAS + 1):
        if roundtrip == maior_roundtrip:
            pendentes = [
                configuracao
                for configuracao in configuracoes
                if chave_configuracao(configuracao)
                not in presentes_na_ultima
            ]
        else:
            pendentes = list(configuracoes)

        if not pendentes:
            print(
                f"roundtrip={roundtrip} já está completa; ignorando.",
                flush=True,
            )
            continue

        print(
            f"roundtrip={roundtrip}: {len(pendentes)} de "
            f"{len(configuracoes)} configuração(ões) pendente(s).",
            flush=True,
        )

        GERADOR_ALEATORIO.shuffle(pendentes)
        print(
            f"roundtrip={roundtrip}: ordem das configurações embaralhada.",
            flush=True,
        )

        for configuracao in pendentes:
            print(
                f"roundtrip={roundtrip}",
                configuracao.identificador,
                configuracao.larguras_mhz,
            )

            arquivos = gerar_yamls(configuracao, roundtrip)
            processos = None
            try:
                montar_topologia(configuracao)
                processos = iniciar_gnb_e_rus(configuracao, arquivos)
                coletar_metricas(
                    configuracao,
                    roundtrip,
                )
            finally:
                if processos is not None:
                    encerrar_processos(processos)
                desmontar_topologia(configuracao)



if __name__ == "__main__":
    main()
