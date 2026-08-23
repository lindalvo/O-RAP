"""Geração das configurações experimentais de fanout e carga."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from itertools import combinations_with_replacement
from math import sqrt

from common.constantes import LARGURAS_MHZ, MAXIMO_AGREGADO_MHZ, MAXIMO_RUS


@dataclass(frozen=True)
class Configuracao:
    numero: int
    quantidade_rus: int
    larguras_mhz: tuple[int, ...]
    carga_agregada_mhz: int
    desvio_padrao_mhz: float

    @property
    def identificador(self) -> str:
        return (
            f"cfg_{self.numero:04d}_"
            f"{self.quantidade_rus}ru_"
            f"{self.carga_agregada_mhz}mhz_"
            f"dp{self.desvio_padrao_mhz:.6f}"
        )


def _variancia_populacional_exata(bandas: tuple[int, ...]) -> Fraction:
    """Calcula a variância populacional sem erros de ponto flutuante."""
    quantidade = len(bandas)
    soma = sum(bandas)
    soma_quadrados = sum(banda**2 for banda in bandas)
    return Fraction(
        quantidade * soma_quadrados - soma**2,
        quantidade**2,
    )


def gerar_configuracoes() -> list[Configuracao]:
    configuracoes: list[Configuracao] = []
    numero = 0

    for quantidade_rus in range(1, MAXIMO_RUS + 1):
        # A chave inclui a variância exata. Assim, para a mesma quantidade de
        # RUs e carga agregada, distribuições com o mesmo desvio padrão não são
        # repetidas, mesmo que suas bandas sejam diferentes.
        configuracoes_unicas: dict[
            tuple[int, Fraction], tuple[int, ...]
        ] = {}

        for bandas_crescentes in combinations_with_replacement(
            LARGURAS_MHZ, quantidade_rus
        ):
            carga_agregada = sum(bandas_crescentes)

            if carga_agregada <= MAXIMO_AGREGADO_MHZ:
                bandas_decrescentes = tuple(reversed(bandas_crescentes))
                variancia = _variancia_populacional_exata(
                    bandas_decrescentes
                )
                chave = (carga_agregada, variancia)
                configuracoes_unicas.setdefault(chave, bandas_decrescentes)

        for carga_agregada, variancia in sorted(configuracoes_unicas):
            numero += 1
            configuracoes.append(
                Configuracao(
                    numero=numero,
                    quantidade_rus=quantidade_rus,
                    larguras_mhz=configuracoes_unicas[
                        (carga_agregada, variancia)
                    ],
                    carga_agregada_mhz=carga_agregada,
                    desvio_padrao_mhz=sqrt(float(variancia)),
                )
            )

    return configuracoes
