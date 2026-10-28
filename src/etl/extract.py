"""Cliente asíncrono de Open-Meteo con reintentos y control de latencia."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from src.config import get_settings

logger = logging.getLogger(__name__)

HOURLY_VARIABLES = (
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "et0_fao_evapotranspiration",
    "soil_moisture_0_to_7cm",
)
_HEADERS = {
    "User-Agent": "GeoPulseETL/1.0 (agroclimate monitoring; educational portfolio)",
    "Accept": "application/json",
}
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class ExtractError(RuntimeError):
    """La fuente meteorológica no pudo entregarse tras los reintentos."""


@dataclass(frozen=True, slots=True)
class ZoneTarget:
    """Zona que el extractor debe consultar."""

    id: int
    name: str
    region: str
    latitude: float
    longitude: float
    crop_or_land_type: str


def as_utc_naive(value: datetime) -> datetime:
    """Normaliza un instante a UTC sin tzinfo (contrato interno del ETL)."""
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.replace(microsecond=0)


def parse_forecast_payload(payload: dict[str, Any] | list[Any], zones: list[ZoneTarget]) -> list[dict[str, Any]]:
    """Aplana la respuesta de Open-Meteo a observaciones por zona y hora.

    Acepta el objeto de una sola coordenada y la lista que devuelve la API
    cuando se piden varias localizaciones. Si la marca de tiempo actual
    coincide con una hora, se conserva la lectura instantánea.
    """
    locations = _coerce_locations(payload)
    if len(locations) != len(zones):
        logger.warning(
            "Open-Meteo devolvió %d localizaciones para %d zonas; se empareja por cercanía",
            len(locations),
            len(zones),
        )
    pairs = _match_locations(locations, zones)
    rows: list[dict[str, Any]] = []
    for location, zone in pairs:
        if location.get("error") and "hourly" not in location:
            logger.warning("zona %s sin datos | %s", zone.name, location.get("reason"))
            continue
        rows.extend(_rows_for_location(location, zone))
    return _prefer_current_on_conflict(rows)


async def extract_observations(zones: list[ZoneTarget]) -> list[dict[str, Any]]:
    """Descarga el bloque horario y el estado actual de todas las zonas."""
    if not zones:
        return []

    settings = get_settings()
    params = {
        "latitude": ",".join(f"{zone.latitude:.4f}" for zone in zones),
        "longitude": ",".join(f"{zone.longitude:.4f}" for zone in zones),
        "hourly": ",".join(HOURLY_VARIABLES),
        "current": ",".join(HOURLY_VARIABLES),
        "past_days": 1,
        "forecast_days": 2,
        "timezone": "UTC",
    }
    timeout = httpx.Timeout(settings.http_timeout_seconds, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, headers=_HEADERS) as client:
        response, elapsed_ms = await _request_with_retry(client, settings.open_meteo_base_url, params)

    try:
        payload = response.json()
    except ValueError as exc:
        raise ExtractError("Open-Meteo devolvió un cuerpo que no es JSON") from exc

    rows = parse_forecast_payload(payload, zones)
    generation_ms = _generation_time_ms(payload)
    logger.info(
        "extracción Open-Meteo | status=%s elapsed_ms=%.1f generation_ms=%s zonas=%d observaciones=%d",
        response.status_code,
        elapsed_ms,
        f"{generation_ms:.1f}" if generation_ms is not None else "n/a",
        len(zones),
        len(rows),
    )
    if elapsed_ms > 5_000:
        logger.warning("Open-Meteo lento | elapsed_ms=%.0f", elapsed_ms)
    return rows


async def _request_with_retry(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, Any],
) -> tuple[httpx.Response, float]:
    settings = get_settings()
    delay = 1.0
    last_error: Exception | None = None

    for attempt in range(1, settings.http_max_retries + 1):
        started = time.perf_counter()
        try:
            response = await client.get(url, params=params)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            last_error = exc
            logger.warning(
                "Open-Meteo intento %d/%d falló | elapsed_ms=%.1f error=%s",
                attempt,
                settings.http_max_retries,
                elapsed_ms,
                exc.__class__.__name__,
            )
            if attempt == settings.http_max_retries:
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, 8.0)
            continue

        elapsed_ms = (time.perf_counter() - started) * 1000
        if response.status_code in _RETRYABLE_STATUS:
            retry_after = _retry_after_seconds(response, fallback=delay)
            logger.warning(
                "Open-Meteo intento %d/%d | status=%s elapsed_ms=%.1f retry_in=%.1fs",
                attempt,
                settings.http_max_retries,
                response.status_code,
                elapsed_ms,
                retry_after,
            )
            last_error = ExtractError(f"Open-Meteo HTTP {response.status_code}")
            if attempt == settings.http_max_retries:
                break
            await asyncio.sleep(retry_after)
            delay = min(delay * 2, 8.0)
            continue

        if response.status_code >= 400:
            body = response.text[:300].replace("\n", " ")
            raise ExtractError(f"Open-Meteo HTTP {response.status_code}: {body}")

        logger.info(
            "Open-Meteo respuesta | status=%s elapsed_ms=%.1f intento=%d",
            response.status_code,
            elapsed_ms,
            attempt,
        )
        return response, elapsed_ms

    raise ExtractError(f"Open-Meteo no respondió tras {settings.http_max_retries} intentos: {last_error}")


def _retry_after_seconds(response: httpx.Response, fallback: float) -> float:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return fallback
    try:
        return max(float(raw), 0.0)
    except ValueError:
        return fallback


def _coerce_locations(payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        if payload.get("error") and "hourly" not in payload:
            reason = payload.get("reason") or "error sin detalle"
            raise ExtractError(f"Open-Meteo rechazó la consulta: {reason}")
        return [payload]
    locations = [item for item in payload if isinstance(item, dict)]
    if not locations:
        raise ExtractError("Open-Meteo devolvió una lista de localizaciones vacía")
    return locations


def _match_locations(
    locations: list[dict[str, Any]],
    zones: list[ZoneTarget],
) -> list[tuple[dict[str, Any], ZoneTarget]]:
    if len(locations) == len(zones):
        return list(zip(locations, zones, strict=True))

    remaining = list(zones)
    pairs: list[tuple[dict[str, Any], ZoneTarget]] = []
    for location in locations:
        lat = float(location.get("latitude", 0.0))
        lon = float(location.get("longitude", 0.0))
        nearest = min(
            remaining,
            key=lambda zone: (zone.latitude - lat) ** 2 + (zone.longitude - lon) ** 2,
        )
        remaining.remove(nearest)
        pairs.append((location, nearest))
        if not remaining:
            break
    return pairs


def _rows_for_location(location: dict[str, Any], zone: ZoneTarget) -> list[dict[str, Any]]:
    hourly = location.get("hourly") or {}
    times = list(hourly.get("time") or [])
    rows: list[dict[str, Any]] = []
    for index, stamp in enumerate(times):
        rows.append(
            {
                "zone_id": zone.id,
                "timestamp": _parse_utc(str(stamp)),
                "temp_c": _at(hourly, "temperature_2m", index),
                "humidity_pct": _at(hourly, "relative_humidity_2m", index),
                "precipitation_mm": _at(hourly, "precipitation", index),
                "et0_mm": _at(hourly, "et0_fao_evapotranspiration", index),
                "soil_moisture_m3m3": _at(hourly, "soil_moisture_0_to_7cm", index),
                "kind": "hourly",
            }
        )

    current = location.get("current") or {}
    if current.get("time"):
        rows.append(
            {
                "zone_id": zone.id,
                "timestamp": _parse_utc(str(current["time"])),
                "temp_c": current.get("temperature_2m"),
                "humidity_pct": current.get("relative_humidity_2m"),
                "precipitation_mm": current.get("precipitation"),
                "et0_mm": current.get("et0_fao_evapotranspiration"),
                "soil_moisture_m3m3": current.get("soil_moisture_0_to_7cm"),
                "kind": "current",
            }
        )
    return rows


def _prefer_current_on_conflict(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(
        rows,
        key=lambda row: (row["zone_id"], row["timestamp"], 1 if row.get("kind") == "current" else 0),
    )
    deduped: dict[tuple[int, datetime], dict[str, Any]] = {}
    for row in ranked:
        deduped[(row["zone_id"], row["timestamp"])] = row
    return list(deduped.values())


def _at(block: dict[str, Any], key: str, index: int) -> float | None:
    values = block.get(key) or []
    if index >= len(values):
        return None
    value = values[index]
    if value is None:
        return None
    return float(value)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return as_utc_naive(parsed)


def _generation_time_ms(payload: dict[str, Any] | list[Any]) -> float | None:
    source: dict[str, Any] | None
    if isinstance(payload, dict):
        source = payload
    elif payload and isinstance(payload[0], dict):
        source = payload[0]
    else:
        source = None
    if source is None or "generationtime_ms" not in source:
        return None
    return float(source["generationtime_ms"])
