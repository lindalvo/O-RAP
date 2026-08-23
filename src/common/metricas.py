"""Helpers compartilhados para processamento das métricas da gNB."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

SEPARADOR_CENARIOS = "|"


def separar_cenarios(valor: str) -> list[str]:
    """Retorna os cenários individuais, sem vazios nem repetições."""
    return list(
        dict.fromkeys(
            cenario.strip()
            for cenario in valor.split(SEPARADOR_CENARIOS)
            if cenario.strip()
        )
    )


def emitir_estado(estado: str, **dados: Any) -> None:
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


__all__ = [
    "SEPARADOR_CENARIOS",
    "separar_cenarios",
    "emitir_estado",
    "normalizar_timestamp_utc",
    "_adicionar",
    "extrair_amostras",
]
