from configuracoes import Topologia
from rede import remover_topologia


def desmontar_topologia(topologia: Topologia) -> None:
    """Remove os recursos de rede associados à topologia executada."""
    remover_topologia(
        topologia.quantidade_rus,
        titulo=(
            f"Desmontagem: {topologia.cenario}/"
            f"{topologia.identificador}"
        ),
    )
