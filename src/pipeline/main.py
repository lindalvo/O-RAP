"""Laço principal do pipeline experimental."""

from coleta import coletar_metricas
from configuracoes import gerar_configuracoes
from desmontagem import desmontar_topologia
from encerramento import encerrar_processos
from processos import iniciar_gnb_e_rus
from topologia import montar_topologia
from yamls import gerar_yamls


RODADAS = 2


def main() -> None:
    for roundtrip in range(1, RODADAS + 1):
        for configuracao in gerar_configuracoes():
            print(
                f"roundtrip={roundtrip}",
                configuracao.identificador,
                configuracao.larguras_mhz,
            )

            arquivos = gerar_yamls(configuracao)
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
