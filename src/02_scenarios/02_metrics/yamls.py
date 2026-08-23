"""Geração dos YAMLs da gNB e das O-RUs emuladas."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from configuracoes import Topologia


DIRETORIO_MAIN = Path(__file__).resolve().parent
DIRETORIO_ASSETS = (DIRETORIO_MAIN / "../../assets").resolve()
DIRETORIO_OUT = (DIRETORIO_MAIN / "../../OUT").resolve()
PADRAO_PLACEHOLDER = re.compile(r"__[A-Z0-9_]+__")


@dataclass(frozen=True, slots=True)
class ArquivosConfiguracao:
    """Caminhos gerados para a execução de uma topologia."""

    diretorio: Path
    gnb_yaml: Path
    rus_yaml: tuple[Path, ...]


def _renderizar(
    matriz: Path,
    destino: Path,
    substituicoes: dict[str, str],
) -> None:
    """Substitui os marcadores de uma matriz e grava o YAML resultante."""
    conteudo = matriz.read_text(encoding="utf-8")

    for marcador, valor in substituicoes.items():
        conteudo = conteudo.replace(marcador, valor)

    pendentes = sorted(set(PADRAO_PLACEHOLDER.findall(conteudo)))
    if pendentes:
        raise ValueError(
            f"Placeholders não substituídos em {matriz.name}: {pendentes}"
        )

    destino.write_text(conteudo, encoding="utf-8")


def gerar_yamls(
    topologia: Topologia,
    roundtrip: int,
    diretorio_assets: Path = DIRETORIO_ASSETS,
    diretorio_out: Path = DIRETORIO_OUT,
) -> ArquivosConfiguracao:
    """Gera os YAMLs preservando a ordem e as bandas das O-RUs."""
    bandas = tuple(ru.largura_banda for ru in topologia.rus)

    diretorio = (
        diretorio_out
        / f"roundtrip_{roundtrip:02d}"
        / topologia.cenario
        / topologia.identificador
    )
    diretorio.mkdir(parents=True, exist_ok=True)

    gnb_yaml = diretorio / "gnb.yml"
    gnb_log = diretorio / "gnb.log"
    substituicoes_gnb = {
        "__GNB_LOG_FILENAME__": str(gnb_log),
        **{
            f"__RU{indice}BW__": banda
            for indice, banda in enumerate(bandas, start=1)
        },
    }
    _renderizar(
        diretorio_assets
        / f"GNB_{topologia.quantidade_rus}RU_matriz.yml",
        gnb_yaml,
        substituicoes_gnb,
    )

    rus_yaml: list[Path] = []
    for indice, banda in enumerate(bandas, start=1):
        ru_yaml = diretorio / f"ru{indice}.yml"
        ru_log = diretorio / f"ru{indice}.log"
        _renderizar(
            diretorio_assets / f"ru{indice}_matriz.yml",
            ru_yaml,
            {
                "__BW__": banda,
                "__RU_LOG_FILENAME__": str(ru_log),
            },
        )
        rus_yaml.append(ru_yaml)

    return ArquivosConfiguracao(
        diretorio=diretorio,
        gnb_yaml=gnb_yaml,
        rus_yaml=tuple(rus_yaml),
    )
