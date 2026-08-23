"""Coleta atômica das métricas JSON disponibilizadas pela gNB.

A parte comum de parsing/normalização fica em common.metricas e permanece
centralizada para evitar divergência entre os pipelines.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from banco import ARQUIVO_BANCO, upsert_scenario
from common.metricas import (
    emitir_estado,
    extrair_amostras,
    separar_cenarios,
)
from configuracoes import Topologia
from erros import ErroColeta


URL_METRICAS = "ws://127.0.0.1:8001"
DURACAO_COLETA_SEGUNDOS = 30
TIMEOUT_CONEXAO_SEGUNDOS = 10


def _identificar_amostras(
    topologia: Topologia,
    roundtrip: int,
    amostras: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Replica a coleta para cada cenário simples antes da gravação."""
    return [
        {
            "identificador": topologia.identificador,
            "roundtrip": roundtrip,
            "cluster_id": topologia.identificador,
            "cenario": cenario,
            **amostra,
        }
        for cenario in separar_cenarios(topologia.cenario)
        for amostra in amostras
    ]


def coletar_metricas(
    topologia: Topologia,
    roundtrip: int,
    segundos: int = DURACAO_COLETA_SEGUNDOS,
    db_path: str | Path = ARQUIVO_BANCO,
) -> int:
    """Coleta em memória e grava somente após completar todo o intervalo."""
    try:
        import websocket
        from websocket import (
            WebSocketConnectionClosedException,
            WebSocketException,
            WebSocketTimeoutException,
        )
    except ImportError as exc:
        raise RuntimeError(
            "Dependência ausente: instale websocket-client para a coleta real"
        ) from exc

    ws = None
    amostras: list[dict[str, Any]] = []
    inicio = time.monotonic()
    primeira_amostra_emitida = False

    emitir_estado(
        "CONNECTING",
        cenario=topologia.cenario,
        identificador=topologia.identificador,
        roundtrip=roundtrip,
    )

    try:
        try:
            ws = websocket.create_connection(
                URL_METRICAS,
                timeout=TIMEOUT_CONEXAO_SEGUNDOS,
            )
        except (OSError, WebSocketException) as exc:
            raise ErroColeta(f"connection_failed: {exc!r}") from exc

        try:
            ws.send(json.dumps({"cmd": "metrics_subscribe"}))
        except (OSError, WebSocketException) as exc:
            raise ErroColeta(f"subscription_failed: {exc!r}") from exc

        emitir_estado("SUBSCRIBED")

        while True:
            restante = segundos - (time.monotonic() - inicio)
            if restante <= 0:
                break

            ws.settimeout(min(TIMEOUT_CONEXAO_SEGUNDOS, max(0.1, restante)))
            try:
                mensagem = ws.recv()
            except WebSocketTimeoutException:
                if time.monotonic() - inicio >= segundos:
                    break
                continue
            except (
                OSError,
                WebSocketConnectionClosedException,
                WebSocketException,
            ) as exc:
                tipo = (
                    "connection_closed"
                    if not amostras
                    else "websocket_closed_after_partial_collection"
                )
                raise ErroColeta(f"{tipo}: {exc!r}") from exc

            if not mensagem:
                tipo = (
                    "connection_closed"
                    if not amostras
                    else "websocket_closed_after_partial_collection"
                )
                raise ErroColeta(tipo)

            try:
                novas = extrair_amostras(json.loads(mensagem))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ErroColeta(f"invalid_metrics_message: {exc!r}") from exc

            amostras.extend(novas)
            if novas and not primeira_amostra_emitida:
                emitir_estado("FIRST_SAMPLE")
                primeira_amostra_emitida = True

        if not amostras:
            raise ErroColeta("collection_finished_without_samples")

    except ErroColeta as exc:
        emitir_estado(
            "ERROR",
            message=repr(str(exc)),
            samples_collected=len(amostras),
        )
        raise
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    amostras_identificadas = _identificar_amostras(
        topologia,
        roundtrip,
        amostras,
    )
    upsert_scenario(amostras_identificadas, db_path)

    quantidade = len(amostras_identificadas)
    emitir_estado(
        "COMPLETED",
        collected_samples=len(amostras),
        database_rows=quantidade,
        scenarios=len(separar_cenarios(topologia.cenario)),
        duration=f"{time.monotonic() - inicio:.2f}",
    )
    return quantidade
