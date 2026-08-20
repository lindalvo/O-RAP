"""Coleta atômica das métricas JSON disponibilizadas pela gNB."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from banco import ARQUIVO_BANCO, upsert_scenario
from configuracoes import Topologia
from erros import ErroColeta


URL_METRICAS = "ws://127.0.0.1:8001"
DURACAO_COLETA_SEGUNDOS = 30
TIMEOUT_CONEXAO_SEGUNDOS = 10
SEPARADOR_CENARIOS = "|"


def separar_cenarios(valor: str) -> list[str]:
    """Retorna os cenários individuais, sem vazios nem repetições."""
    return list(dict.fromkeys(
        cenario.strip()
        for cenario in valor.split(SEPARADOR_CENARIOS)
        if cenario.strip()
    ))


def emitir_estado(estado: str, **dados: Any) -> None:
    """Emite uma linha de estado facilmente identificável nos logs."""
    detalhes = " ".join(f"{chave}={valor}" for chave, valor in dados.items())
    print(f"KPI_STATE={estado} {detalhes}".rstrip(), flush=True)


def normalizar_timestamp_utc(valor: Any) -> str:
    """Normaliza timestamps ISO-8601 ou Unix para UTC com sufixo Z."""
    if isinstance(valor, (int, float)):
        segundos = float(valor)
        if segundos > 10_000_000_000:
            segundos /= 1000
        instante = datetime.fromtimestamp(segundos, tz=timezone.utc)
    elif isinstance(valor, str):
        texto = valor.strip()
        if texto.replace(".", "", 1).isdigit():
            return normalizar_timestamp_utc(float(texto))
        instante = datetime.fromisoformat(texto.replace("Z", "+00:00"))
        if instante.tzinfo is None:
            instante = instante.replace(tzinfo=timezone.utc)
        instante = instante.astimezone(timezone.utc)
    else:
        raise ValueError(f"Timestamp não suportado: {valor!r}")

    return instante.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _adicionar(
    amostras: list[dict[str, Any]],
    timestamp_utc: str,
    metric: str,
    value: Any,
    unit: str,
) -> None:
    if value is not None:
        amostras.append(
            {
                "timestamp_utc": timestamp_utc,
                "metric": metric,
                "value": float(value),
                "unit": unit,
            }
        )


def extrair_amostras(metrica: dict[str, Any]) -> list[dict[str, Any]]:
    """Extrai as métricas utilizadas de uma mensagem JSON da gNB."""
    timestamp_utc = normalizar_timestamp_utc(metrica["timestamp"])
    amostras: list[dict[str, Any]] = []

    ru = metrica.get("ru")
    if ru is not None:
        celulas_ofh = ru["ofh"]["cells"]
        throughput_ul_total = sum(
            celula["ul"]["ethernet_receiver"]["average_throughput_mbps"]
            for celula in celulas_ofh
        )
        throughput_dl_total = sum(
            celula["dl"]["ethernet_transmitter"]["average_throughput_mbps"]
            for celula in celulas_ofh
        )
        _adicionar(
            amostras,
            timestamp_utc,
            "ofh_ul_throughput",
            throughput_ul_total,
            "Mbps",
        )
        _adicionar(
            amostras,
            timestamp_utc,
            "ofh_dl_throughput",
            throughput_dl_total,
            "Mbps",
        )

    recursos = metrica.get("app_resource_usage")
    if recursos is not None:
        _adicionar(
            amostras,
            timestamp_utc,
            "cpu_usage",
            recursos.get("cpu_usage_percent"),
            "%",
        )
        _adicionar(
            amostras,
            timestamp_utc,
            "memory_usage",
            recursos.get("mem_total_mb"),
            "MB",
        )
        _adicionar(
            amostras,
            timestamp_utc,
            "cpu_package_power",
            recursos.get("power_consumption_watts"),
            "W",
        )

    celulas = metrica.get("cells")
    if celulas:
        latencias = [
            celula["cell_metrics"]["max_latency"]
            for celula in celulas
            if celula.get("cell_metrics", {}).get("max_latency") is not None
        ]
        if latencias:
            _adicionar(
                amostras,
                timestamp_utc,
                "max_scheduler_latency",
                max(latencias),
                "µs",
            )

    return amostras


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
