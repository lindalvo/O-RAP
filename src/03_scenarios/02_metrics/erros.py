"""Exceções compartilhadas pelos módulos do pipeline."""


class ErroPipeline(Exception):
    """Classe-base para erros gerados pelo pipeline."""


class ErroExecucaoRecuperavel(ErroPipeline):
    """Falha temporária que permite tentar a topologia novamente."""


class ErroColeta(ErroExecucaoRecuperavel):
    """Falha recuperável durante a conexão ou a coleta de métricas."""


class ErroProcesso(ErroExecucaoRecuperavel):
    """Falha recuperável ao iniciar ou acompanhar gNB e O-RUs."""


class ErroTopologia(ErroExecucaoRecuperavel):
    """Falha recuperável durante a montagem da topologia de rede."""
