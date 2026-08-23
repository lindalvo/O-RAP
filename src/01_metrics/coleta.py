"""Coleta atômica das métricas JSON disponibilizadas pela gNB."""

from __future__ import annotations

import json
import time
import websocket
from websocket import (WebSocketConnectionClosedException, WebSocketException,WebSocketTimeoutException)

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from banco import DB_PATH, gravar_amostras, inicializar_banco
from common.metricas import (
    emitir_estado,
    extrair_amostras,
    normalizar_timestamp_utc,
)
from configuracoes import Configuracao


URL_METRICAS = "ws://127.0.0.1:8001"
DURACAO_COLETA_SEGUNDOS = 30


class ErroColeta(RuntimeError):
    def __init__(self, codigo: int, tipo: str, mensagem: str):
        super().__init__(mensagem)
        self.codigo = codigo
        self.tipo = tipo


def coletar_metricas(
    configuracao: Configuracao,
    roundtrip: int,
    segundos: int = DURACAO_COLETA_SEGUNDOS,
    db_path: Path = DB_PATH
) -> int:
    """Coleta em memória e grava somente após completar todo o intervalo."""
    inicializar_banco(db_path)
    ws = None
    amostras: list[dict[str, Any]] = []
    inicio = time.monotonic()
    primeira_amostra_emitida = False

    emitir_estado("CONNECTING")
    try:
        try:
            ws = websocket.create_connection(URL_METRICAS, timeout=10)
        except (OSError, WebSocketException) as exc:
            raise ErroColeta(10, "connection_failed", repr(exc)) from exc

        try:
            ws.send(json.dumps({"cmd": "metrics_subscribe"}))
        except WebSocketException as exc:
            raise ErroColeta(11, "subscription_failed", repr(exc)) from exc

        emitir_estado("SUBSCRIBED")

        while True:
            restante = segundos - (time.monotonic() - inicio)
            if restante <= 0:
                break

            ws.settimeout(min(10, max(0.1, restante)))
            try:
                mensagem = ws.recv()
            except WebSocketTimeoutException:
                if time.monotonic() - inicio >= segundos:
                    break
                continue
            except WebSocketConnectionClosedException as exc:
                codigo = 12 if not amostras else 13
                tipo = (
                    "connection_closed"
                    if not amostras
                    else "websocket_closed_after_partial_collection"
                )
                raise ErroColeta(codigo, tipo, repr(exc)) from exc

            novas = extrair_amostras(json.loads(mensagem))
            amostras.extend(novas)
            if novas and not primeira_amostra_emitida:
                emitir_estado("FIRST_SAMPLE")
                primeira_amostra_emitida = True
    except ErroColeta as exc:
        emitir_estado(
            "ERROR",
            code=exc.codigo,
            type=exc.tipo,
            message=repr(str(exc)),
            samples_collected=len(amostras),
        )
        raise
    finally:
        if ws is not None:
            ws.close()

    quantidade = gravar_amostras(
        configuracao,
        roundtrip,
        amostras,
        db_path,
    )
    emitir_estado(
        "COMPLETED",
        samples=quantidade,
        duration=f"{time.monotonic() - inicio:.2f}",
    )
    return quantidade
