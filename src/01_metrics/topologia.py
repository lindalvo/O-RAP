"""Geração dos comandos de montagem da topologia O-DU/O-RU (facade para utilitários comuns)."""

from __future__ import annotations

from common.comandos import executar
from common.topology import comandos_da_ru
from configuracoes import Configuracao
from common.rede import remover_topologia


ATRASO_DU_RU_US = 60


def montar_topologia(configuracao: Configuracao) -> None:
    """Monta uma topologia com um namespace e uma veth por O-RU.

    Mantém o comportamento legado do pipeline 01_metrics: aplica netem apenas
    no egress da O-DU.
    """
    remover_topologia(
        configuracao.quantidade_rus,
        titulo="Preparação da topologia",
    )
    print(f"\n=== Montagem: {configuracao.identificador} ===")

    for indice_ru in range(1, configuracao.quantidade_rus + 1):
        for comando in comandos_da_ru(indice_ru, ATRASO_DU_RU_US, ambos_sentidos=False):
            executar(comando)
