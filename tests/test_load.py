"""UPSERT idempotente sobre una base SQLite aislada."""

from __future__ import annotations

from datetime import datetime

import time

import pandas as pd
import pytest
from sqlalchemy import func, select

from src.config import get_settings
from src.database.models import MonitoringZone, TelemetryRecord
from src.database.session import init_db, reset_engine, session_scope
from src.etl.load import load_telemetry


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    database = tmp_path / "geopulse.db"
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + database.resolve().as_posix())
    get_settings.cache_clear()
    reset_engine()
    yield
    reset_engine()
    get_settings.cache_clear()


def _frame(zone_id: int, temp_c: float) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "zone_id": zone_id,
                "timestamp": datetime(2026, 10, 28, 12, 0),
                "temp_c": temp_c,
                "humidity_pct": 40.0,
                "soil_moisture_m3m3": 0.22,
                "et0_mm": 0.30,
                "precipitation_mm": 0.0,
                "vpd_kpa": 1.4,
                "hydric_stress_index": 55.0,
                "risk_level": "HIGH_STRESS",
                "is_anomaly": False,
            }
        ]
    )


@pytest.mark.usefixtures("isolated_db")
def test_upsert_inserts_once_and_updates_the_same_hour() -> None:
    init_db()
    with session_scope() as session:
        zone_id = session.scalar(select(MonitoringZone.id).order_by(MonitoringZone.id).limit(1))
    assert zone_id is not None

    first = load_telemetry(_frame(int(zone_id), 24.0), started_perf=time.perf_counter())
    second = load_telemetry(_frame(int(zone_id), 31.5), started_perf=time.perf_counter())

    assert first.rows_inserted == 1
    assert first.rows_updated == 0
    assert second.rows_inserted == 0
    assert second.rows_updated == 1
    assert first.elapsed_ms >= 0

    with session_scope() as session:
        count = session.scalar(select(func.count()).select_from(TelemetryRecord))
        stored = session.scalar(select(TelemetryRecord.temp_c))
    assert count == 1
    assert stored == pytest.approx(31.5)
