import csv
from dataclasses import dataclass
from pathlib import Path
from common.constantes import DIRETORIO_OUT

ARQUIVO_PIPELINE =DIRETORIO_OUT / "pipeline_RMB.txt"
SEPARADOR = ","


@dataclass(frozen=True, slots=True)
class ORU:
    """Uma O-RU da linha do pipeline."""

    numestacao: str
    largura_banda: str
    delay_us: int


@dataclass(frozen=True, slots=True)
class Topologia:
    """Topologia de uma O-DU em determinado cenário."""

    cenario: str
    rus: tuple[ORU, ...]

    @property
    def identificador(self) -> str:
        """Retorna a numestacao da O-DU, presente no primeiro trio."""
        return self.rus[0].numestacao

    @property
    def quantidade_rus(self) -> int:
        """Retorna a quantidade de O-RUs associadas à O-DU."""
        return len(self.rus)


def _converter_linha(campos: list[str]) -> Topologia:
    """Converte uma linha já separada em uma topologia."""
    cenario = campos[0].strip()
    valores = campos[1:]

    rus = tuple(
        ORU(
            numestacao=valores[indice].strip(),
            largura_banda=valores[indice + 1].strip(),
            delay_us=int(valores[indice + 2].strip()),
        )
        for indice in range(0, len(valores), 3)
    )

    return Topologia(cenario=cenario, rus=rus)


def carregar_topologias(
    caminho: str | Path = ARQUIVO_PIPELINE,
) -> list[Topologia]:
    """Lê o arquivo de pipeline e devolve suas topologias na ordem original."""
    with Path(caminho).open(encoding="utf-8", newline="") as arquivo:
        leitor = csv.reader(arquivo, delimiter=SEPARADOR)
        return [_converter_linha(linha) for linha in leitor if linha]
