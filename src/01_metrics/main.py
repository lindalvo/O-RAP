"""Laço principal do pipeline experimental."""

from collections import deque
from random import SystemRandom

from banco import carregar_estado_retomada, chave_configuracao
from coleta import ErroColeta, coletar_metricas
from configuracoes import Configuracao, gerar_configuracoes
from desmontagem import desmontar_topologia
from encerramento import encerrar_processos
from limpeza import limpar_ambiente_residual
from processos import ErroProcesso, iniciar_gnb_e_rus
from topologia import montar_topologia
from yamls import gerar_yamls
from common.constantes import (MAX_TENTATIVAS,RODADAS)

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

        fila = deque(pendentes)
        tentativas = {configuracao: 0 for configuracao in pendentes}
        esgotadas: list[tuple[Configuracao, ErroColeta | ErroProcesso]] = []

        while fila:
            configuracao = fila.popleft()
            tentativas[configuracao] += 1
            tentativa = tentativas[configuracao]
            print(
                f"roundtrip={roundtrip}",
                configuracao.identificador,
                configuracao.larguras_mhz,
                f"tentativa={tentativa}/{MAX_TENTATIVAS}",
                flush=True,
            )

            arquivos = gerar_yamls(configuracao, roundtrip)
            processos = None
            try:
                montar_topologia(configuracao)
                processos = iniciar_gnb_e_rus(configuracao, arquivos)
                coletar_metricas(configuracao, roundtrip)
            except ErroColeta as exc:
                if tentativa < MAX_TENTATIVAS:
                    fila.append(configuracao)
                    print(
                        f"Coleta falhou para {configuracao.identificador} "
                        f"({exc.tipo}, código {exc.codigo}). "
                        "Configuração recolocada no final da fila.",
                        flush=True,
                    )
                else:
                    esgotadas.append((configuracao, exc))
                    print(
                        f"Limite de {MAX_TENTATIVAS} "
                        f"tentativas atingido para "
                        f"{configuracao.identificador}; as demais "
                        "configurações da rodada continuarão.",
                        flush=True,
                    )
            except ErroProcesso as exc:
                if tentativa < MAX_TENTATIVAS:
                    fila.append(configuracao)
                    print(
                        f"Falha ao iniciar processos para "
                        f"{configuracao.identificador}: {exc}. "
                        "Configuração recolocada no final da fila.",
                        flush=True,
                    )
                else:
                    esgotadas.append((configuracao, exc))
                    print(
                        f"Limite de tentativas atingido para "
                        f"{configuracao.identificador}; as demais continuarão.",
                        flush=True,
                    )
            finally:
                if processos is not None:
                    encerrar_processos(processos)
                desmontar_topologia(configuracao)

        if esgotadas:
            identificadores = ", ".join(
                configuracao.identificador
                for configuracao, _ in esgotadas
            )
            raise RuntimeError(
                f"roundtrip={roundtrip} não foi concluída: "
                f"{len(esgotadas)} configuração(ões) falharam após "
                f"{MAX_TENTATIVAS} tentativas: "
                f"{identificadores}. O pipeline não avançará para a "
                "rodada seguinte; uma reinicialização retomará as "
                "configurações ausentes."
            )



if __name__ == "__main__":
    main()
