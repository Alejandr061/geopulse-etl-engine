"""Carga idempotente de telemetría (UPSERT / ON CONFLICT DO UPDATE)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.models import TelemetryRecord
from src.database.session import session_scope
from src.etl.extract import as_utc_naive

logger = logging.getLogger(__name__)

_UPDATE_COLUMNS = (
    "temp_c",
    "humidity_pct",
    "soil_moisture_m3m3",
    "et0_mm",
    "precipitation_mm",
    "vpd_kpa",
    "hydric_stress_index",
    "risk_level",
    "is_anomaly",
)
_CHUNK_SIZE = 150


@dataclass(slots=True)
class LoadResult:
    """Balance de la escritura y duración total del pipeline."""

    rows_inserted: int
    rows_updated: int
    elapsed_ms: float


def load_telemetry(frame: pd.DataFrame, started_perf: float) -> LoadResult:
    """Inserta o actualiza la telemetría y registra el balance del pipeline.

    `started_perf` es el `perf_counter` del inicio del pipeline, de modo que
    el log de carga incluye extracción, transformación y escritura.
    """
    records = _frame_to_records(frame)
    inserted = 0
    updated = 0
    with session_scope() as session:
        if records:
            existing = _existing_keys(session, records)
            inserted = sum(1 for record in records if _key(record) not in existing)
            updated = len(records) - inserted
            _upsert(session, records)

    elapsed_ms = (time.perf_counter() - started_perf) * 1000.0
    logger.info(
        "carga ETL completa | inserted=%d updated=%d elapsed_ms=%.2f",
        inserted,
        updated,
        elapsed_ms,
    )
    return LoadResult(inserted, updated, round(elapsed_ms, 2))


def _frame_to_records(frame: pd.DataFrame) -> list[dict[str, object]]:
    if frame.empty:
        return []
    records: list[dict[str, object]] = []
    for row in frame.itertuples(index=False):
        timestamp = row.timestamp
        if isinstance(timestamp, pd.Timestamp):
            timestamp = timestamp.to_pydatetime()
        records.append(
            {
                "zone_id": int(row.zone_id),
                "timestamp": as_utc_naive(timestamp),
                "temp_c": float(row.temp_c),
                "humidity_pct": float(row.humidity_pct),
                "soil_moisture_m3m3": float(row.soil_moisture_m3m3),
                "et0_mm": float(row.et0_mm),
                "precipitation_mm": float(row.precipitation_mm),
                "vpd_kpa": float(row.vpd_kpa),
                "hydric_stress_index": float(row.hydric_stress_index),
                "risk_level": str(row.risk_level),
                "is_anomaly": bool(row.is_anomaly),
            }
        )
    return records


def _key(record: dict[str, object]) -> tuple[int, datetime]:
    timestamp = record["timestamp"]
    assert isinstance(timestamp, datetime)
    return int(record["zone_id"]), as_utc_naive(timestamp)


def _existing_keys(session: Session, records: list[dict[str, object]]) -> set[tuple[int, datetime]]:
    zone_ids = {int(record["zone_id"]) for record in records}
    timestamps = [record["timestamp"] for record in records if isinstance(record["timestamp"], datetime)]
    min_ts = min(timestamps)
    max_ts = max(timestamps)
    found = session.execute(
        select(TelemetryRecord.zone_id, TelemetryRecord.timestamp).where(
            TelemetryRecord.zone_id.in_(zone_ids),
            TelemetryRecord.timestamp >= min_ts,
            TelemetryRecord.timestamp <= max_ts,
        )
    ).all()
    return {(int(zone_id), as_utc_naive(stamp)) for zone_id, stamp in found}


def _upsert(session: Session, records: list[dict[str, object]]) -> None:
    insert = _insert_builder(session)
    for start in range(0, len(records), _CHUNK_SIZE):
        chunk = records[start : start + _CHUNK_SIZE]
        statement = insert(TelemetryRecord).values(chunk)
        statement = statement.on_conflict_do_update(
            index_elements=["zone_id", "timestamp"],
            set_={column: getattr(statement.excluded, column) for column in _UPDATE_COLUMNS},
        )
        session.execute(statement)


def _insert_builder(session: Session):
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        return pg_insert
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        return sqlite_insert
    raise RuntimeError(f"UPSERT no implementado para el dialecto '{dialect}'")
