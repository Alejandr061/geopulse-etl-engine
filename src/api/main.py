"""API geoespacial y centro de control de GeoPulse ETL."""

from __future__ import annotations

import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.config import safe_database_label, setup_logging
from src.database.models import MonitoringZone, TelemetryRecord
from src.database.session import init_db, session_scope
from src.etl.extract import ExtractError
from src.etl.pipeline import run_pipeline

logger = logging.getLogger(__name__)

DASHBOARD_PATH = Path(__file__).resolve().parent / "dashboard.html"
HISTORY_HOURS = 48
FORECAST_HOURS = 24


class ZoneSnapshotOut(BaseModel):
    """Lectura vigente de una zona tras la corrida."""

    zone_id: int
    name: str
    region: str
    risk_level: str
    hydric_stress_index: float
    vpd_kpa: float
    temp_c: float
    is_anomaly: bool


class PipelineMetrics(BaseModel):
    """Métricas de rendimiento de una ejecución del ETL."""

    zones: int
    rows_received: int
    rows_discarded: int
    rows_imputed: int
    rows_inserted: int
    rows_updated: int
    anomalies: int
    elapsed_ms: float
    source: str
    zones_latest: list[ZoneSnapshotOut]


class SeriesPoint(BaseModel):
    """Punto horario de la serie de una zona."""

    timestamp: datetime
    temp_c: float
    humidity_pct: float
    soil_moisture_m3m3: float
    et0_mm: float
    precipitation_mm: float
    vpd_kpa: float
    hydric_stress_index: float
    risk_level: str
    is_anomaly: bool
    is_forecast: bool


class SeriesResponse(BaseModel):
    """Historia reciente y pronóstico de 24 h de una zona."""

    zone_id: int
    name: str
    region: str
    latitude: float
    longitude: float
    elevation: float
    crop_or_land_type: str
    points: list[SeriesPoint] = Field(default_factory=list)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging()
    init_db()
    logger.info("GeoPulse API lista | database=%s", safe_database_label())
    yield


app = FastAPI(
    title="GeoPulse ETL",
    summary="Motor geoespacial de inteligencia agroclimática y ambiental en tiempo real.",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard() -> HTMLResponse:
    """Sirve el centro de control cartográfico."""
    if not DASHBOARD_PATH.exists():
        raise HTTPException(status_code=500, detail="Falta el dashboard")
    return HTMLResponse(
        DASHBOARD_PATH.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/v1/etl/trigger", response_model=PipelineMetrics)
async def trigger_etl() -> PipelineMetrics:
    """Ejecuta una corrida del ETL y devuelve filas escritas y duración."""
    try:
        report = await run_pipeline()
    except ExtractError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return PipelineMetrics.model_validate(asdict(report))


@app.get("/api/v1/geojson/latest")
def latest_geojson() -> JSONResponse:
    """Último estado de cada zona como `FeatureCollection` GeoJSON.

    La geometría es un `Point` en orden `[longitud, latitud]`. Las métricas
    climáticas y biofísicas viajan en `properties`.
    """
    with session_scope() as session:
        payload = _feature_collection(session)
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@app.get("/api/v1/zones/{zone_id}/timeseries", response_model=SeriesResponse)
def zone_timeseries(zone_id: int) -> SeriesResponse:
    """Serie histórica reciente y pronóstico de las próximas 24 horas."""
    now = _utcnow()
    start = now - timedelta(hours=HISTORY_HOURS)
    end = now + timedelta(hours=FORECAST_HOURS)
    with session_scope() as session:
        zone = session.get(MonitoringZone, zone_id)
        if zone is None:
            raise HTTPException(status_code=404, detail=f"Zona {zone_id} no encontrada")
        rows = session.scalars(
            select(TelemetryRecord)
            .where(
                TelemetryRecord.zone_id == zone_id,
                TelemetryRecord.timestamp >= start,
                TelemetryRecord.timestamp <= end,
            )
            .order_by(TelemetryRecord.timestamp)
        ).all()
        return SeriesResponse(
            zone_id=zone.id,
            name=zone.name,
            region=zone.region,
            latitude=zone.latitude,
            longitude=zone.longitude,
            elevation=zone.elevation,
            crop_or_land_type=zone.crop_or_land_type,
            points=[_series_point(row, now) for row in rows],
        )


def _feature_collection(session) -> dict[str, object]:
    zones = session.scalars(select(MonitoringZone).order_by(MonitoringZone.id)).all()
    records = session.scalars(select(TelemetryRecord)).all()
    grouped: dict[int, list[TelemetryRecord]] = defaultdict(list)
    for record in records:
        grouped[record.zone_id].append(record)

    now = _utcnow()
    features = []
    for zone in zones:
        latest = _pick_latest(grouped.get(zone.id, []), now)
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [round(zone.longitude, 6), round(zone.latitude, 6)],
                },
                "properties": _properties(zone, latest),
            }
        )
    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "zone_count": len(features),
            "with_telemetry": sum(1 for feature in features if feature["properties"]["has_telemetry"]),
        },
    }


def _pick_latest(records: list[TelemetryRecord], now: datetime) -> TelemetryRecord | None:
    if not records:
        return None
    past = [record for record in records if record.timestamp <= now]
    if past:
        return max(past, key=lambda record: record.timestamp)
    return min(records, key=lambda record: record.timestamp)


def _properties(zone: MonitoringZone, record: TelemetryRecord | None) -> dict[str, object]:
    properties: dict[str, object] = {
        "zone_id": zone.id,
        "name": zone.name,
        "region": zone.region,
        "latitude": zone.latitude,
        "longitude": zone.longitude,
        "elevation": zone.elevation,
        "crop_or_land_type": zone.crop_or_land_type,
        "has_telemetry": record is not None,
    }
    if record is None:
        properties.update(
            {
                "timestamp": None,
                "temp_c": None,
                "humidity_pct": None,
                "soil_moisture_m3m3": None,
                "et0_mm": None,
                "precipitation_mm": None,
                "vpd_kpa": None,
                "hydric_stress_index": None,
                "risk_level": None,
                "is_anomaly": False,
            }
        )
        return properties

    properties.update(
        {
            "timestamp": _aware(record.timestamp).isoformat(),
            "temp_c": record.temp_c,
            "humidity_pct": record.humidity_pct,
            "soil_moisture_m3m3": record.soil_moisture_m3m3,
            "et0_mm": record.et0_mm,
            "precipitation_mm": record.precipitation_mm,
            "vpd_kpa": record.vpd_kpa,
            "hydric_stress_index": record.hydric_stress_index,
            "risk_level": record.risk_level,
            "is_anomaly": bool(record.is_anomaly),
        }
    )
    return properties


def _series_point(record: TelemetryRecord, now: datetime) -> SeriesPoint:
    return SeriesPoint(
        timestamp=_aware(record.timestamp),
        temp_c=record.temp_c,
        humidity_pct=record.humidity_pct,
        soil_moisture_m3m3=record.soil_moisture_m3m3,
        et0_mm=record.et0_mm,
        precipitation_mm=record.precipitation_mm,
        vpd_kpa=record.vpd_kpa,
        hydric_stress_index=record.hydric_stress_index,
        risk_level=record.risk_level,
        is_anomaly=bool(record.is_anomaly),
        is_forecast=record.timestamp > now,
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
