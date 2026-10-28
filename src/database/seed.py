"""Precarga idempotente de la malla mediterránea."""

from __future__ import annotations

import logging

from sqlalchemy import select

from src.database.grid import MEDITERRANEAN_GRID, GridNode
from src.database.models import MonitoringZone
from src.database.session import session_scope

logger = logging.getLogger(__name__)


def seed_monitoring_zones() -> int:
    """Inserta los nodos que todavía no existen. Devuelve cuántos creó."""
    inserted = 0
    with session_scope() as session:
        existing = set(session.scalars(select(MonitoringZone.name)).all())
        for node in MEDITERRANEAN_GRID:
            if node.name in existing:
                continue
            session.add(_zone_from_node(node))
            inserted += 1
    if inserted:
        logger.info("malla geoespacial precargada | nuevas_zonas=%d", inserted)
    else:
        logger.info("malla geoespacial ya presente | zonas=%d", len(MEDITERRANEAN_GRID))
    return inserted


def _zone_from_node(node: GridNode) -> MonitoringZone:
    return MonitoringZone(
        name=node.name,
        region=node.region,
        latitude=node.latitude,
        longitude=node.longitude,
        elevation=node.elevation,
        crop_or_land_type=node.crop_or_land_type,
    )
