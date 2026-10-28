"""Esquema relacional de zonas de monitoreo y telemetría agroclimática."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base declarativa de SQLAlchemy 2.0."""


class MonitoringZone(Base):
    """Nodo geoespacial fijo de la malla de observación.

    `latitude` y `longitude` están en WGS84. `elevation` está en metros.
    """

    __tablename__ = "monitoring_zones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    region: Mapped[str] = mapped_column(String(120), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    elevation: Mapped[float] = mapped_column(Float, nullable=False)
    crop_or_land_type: Mapped[str] = mapped_column(String(160), nullable=False)

    telemetry: Mapped[list[TelemetryRecord]] = relationship(
        back_populates="zone",
        cascade="all, delete-orphan",
    )


class TelemetryRecord(Base):
    """Observación horaria o instantánea ya transformada.

    La restricción única `(zone_id, timestamp)` hace idempotente el ETL:
    volver a cargar la misma hora actualiza la fila en lugar de duplicarla.
    Todos los timestamps se persisten en UTC.
    """

    __tablename__ = "telemetry_records"
    __table_args__ = (
        UniqueConstraint("zone_id", "timestamp", name="uq_telemetry_zone_timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zone_id: Mapped[int] = mapped_column(
        ForeignKey("monitoring_zones.id", ondelete="CASCADE"),
        nullable=False,
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    temp_c: Mapped[float] = mapped_column(Float, nullable=False)
    humidity_pct: Mapped[float] = mapped_column(Float, nullable=False)
    soil_moisture_m3m3: Mapped[float] = mapped_column(Float, nullable=False)
    et0_mm: Mapped[float] = mapped_column(Float, nullable=False)
    precipitation_mm: Mapped[float] = mapped_column(Float, nullable=False)
    vpd_kpa: Mapped[float] = mapped_column(Float, nullable=False)
    hydric_stress_index: Mapped[float] = mapped_column(Float, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    is_anomaly: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    zone: Mapped[MonitoringZone] = relationship(back_populates="telemetry")
