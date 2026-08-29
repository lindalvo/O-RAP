"""Montagem da rede O-DU/O-RU para uma topologia experimental.

Reúne a implementação comum de criação de namespaces/veths e mantém o
comportamento experimental do pipeline 02_metrics, que aplica netem em ambos
os sentidos da ligação.
"""

from __future__ import annotations

from common.comandos import executar
from common.topology import comandos_da_ru
from configuracoes import Topologia
from erros import ErroTopologia
from common.rede import remover_topologia
from common.constantes import ATRASO_DU_RU_US


def montar_topologia(topologia: Topologia) -> None:
    """Monta um namespace e um par veth para cada O-RU da topologia."""
    remover_topologia(
        topologia.quantidade_rus,
        titulo="Preparação da topologia",
    )
    print(
        f"\n=== Montagem: {topologia.cenario}/"
        f"{topologia.identificador} ===",
        flush=True,
    )

    try:
        for indice_ru, ru in enumerate(topologia.rus, start=1):
            atraso_total_us = ATRASO_FIXO_US + ru.delay_us
            print(
                f"O-RU {indice_ru}: numestacao={ru.numestacao} "
                f"delay={ATRASO_FIXO_US}+{ru.delay_us}="
                f"{atraso_total_us}us por sentido",
                flush=True,
            )

            for comando in comandos_da_ru(
                indice_ru,
                atraso_total_us,
                ambos_sentidos=True,
            ):
                executar(comando)
    except Exception as exc:  # pragma: no cover - rotina de montagem do kernel.
        raise ErroTopologia(
            f"Falha ao montar {topologia.cenario}/"
            f"{topologia.identificador}: {exc}"
        ) from exc
