"""Orquestación del pipeline extract → transform → load."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import select

from src.database.models import MonitoringZone
from src.database.session import init_db, session_scope
from src.etl.extract import ZoneTarget, extract_observations
from src.etl.load import load_telemetry
from src.etl.transform import transform_observations

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ZoneSnapshot:
    """Última lectura, ya transformada, de una zona."""

    zone_id: int
    name: str
    region: str
    risk_level: str
    hydric_stress_index: float
    vpd_kpa: float
    temp_c: float
    is_anomaly: bool


@dataclass(slots=True)
class PipelineReport:
    """Métricas de una corrida completa del ETL."""

    zones: int
    rows_received: int
    rows_discarded: int
    rows_imputed: int
    rows_inserted: int
    rows_updated: int
    anomalies: int
    elapsed_ms: float
    source: str
    zones_latest: list[ZoneSnapshot]


def prepare_zones() -> list[ZoneTarget]:
    """Crea el esquema, siembra la malla y devuelve los nodos a extraer."""
    init_db()
    with session_scope() as session:
        rows = session.scalars(select(MonitoringZone).order_by(MonitoringZone.id)).all()
        return [
            ZoneTarget(
                id=zone.id,
                name=zone.name,
                region=zone.region,
                latitude=zone.latitude,
                longitude=zone.longitude,
                crop_or_land_type=zone.crop_or_land_type,
            )
            for zone in rows
        ]


async def run_pipeline() -> PipelineReport:
    """Ejecuta una corrida completa y devuelve sus métricas."""
    started = time_counter()
    zones = await asyncio.to_thread(prepare_zones)
    raw_rows = await extract_observations(zones)
    transformed = transform_observations(raw_rows)
    loaded = await asyncio.to_thread(load_telemetry, transformed.frame, started)
    anomalies = 0 if transformed.frame.empty else int(transformed.frame["is_anomaly"].sum())
    report = PipelineReport(
        zones=len(zones),
        rows_received=transformed.rows_received,
        rows_discarded=transformed.rows_discarded,
        rows_imputed=transformed.rows_imputed,
        rows_inserted=loaded.rows_inserted,
        rows_updated=loaded.rows_updated,
        anomalies=anomalies,
        elapsed_ms=loaded.elapsed_ms,
        source="open-meteo",
        zones_latest=_latest_snapshots(transformed.frame, zones),
    )
    logger.info(
        "pipeline finalizado | zonas=%d inserted=%d updated=%d elapsed_ms=%.2f",
        report.zones,
        report.rows_inserted,
        report.rows_updated,
        report.elapsed_ms,
    )
    return report


def run_pipeline_sync() -> PipelineReport:
    """Puente síncrono para la CLI y los workflows."""
    return asyncio.run(run_pipeline())


def time_counter() -> float:
    """Reloj monotónico del inicio del pipeline."""
    import time

    return time.perf_counter()


def _latest_snapshots(frame: pd.DataFrame, zones: list[ZoneTarget]) -> list[ZoneSnapshot]:
    if frame.empty:
        return []
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    by_id = {zone.id: zone for zone in zones}
    snapshots: list[ZoneSnapshot] = []
    ordered = frame.sort_values("timestamp")
    for zone_id, group in ordered.groupby("zone_id", sort=True):
        zone = by_id.get(int(zone_id))
        if zone is None:
            continue
        past = group[group["timestamp"] <= now]
        chosen = past.iloc[-1] if not past.empty else group.iloc[0]
        snapshots.append(
            ZoneSnapshot(
                zone_id=int(zone_id),
                name=zone.name,
                region=zone.region,
                risk_level=str(chosen["risk_level"]),
                hydric_stress_index=float(chosen["hydric_stress_index"]),
                vpd_kpa=float(chosen["vpd_kpa"]),
                temp_c=float(chosen["temp_c"]),
                is_anomaly=bool(chosen["is_anomaly"]),
            )
        )
    return snapshots
