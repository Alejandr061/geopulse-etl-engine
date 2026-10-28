"""Malla de nodos de monitoreo del arco mediterráneo y la España interior.

Coordenadas en WGS84 (grados decimales). La elevación está en metros sobre
el nivel del mar y corresponde al municipio ancla de cada comarca.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GridNode:
    """Nodo fijo de la malla agroclimática."""

    name: str
    region: str
    latitude: float
    longitude: float
    elevation: float
    crop_or_land_type: str


MEDITERRANEAN_GRID: tuple[GridNode, ...] = (
    GridNode(
        name="Plana Baixa",
        region="Castellón (Vila-real)",
        latitude=39.9383,
        longitude=-0.1009,
        elevation=42.0,
        crop_or_land_type="Cítricos",
    ),
    GridNode(
        name="Huerta de Murcia",
        region="Murcia",
        latitude=37.9922,
        longitude=-1.1307,
        elevation=43.0,
        crop_or_land_type="Hortalizas de regadío",
    ),
    GridNode(
        name="Vega del Guadalquivir",
        region="Córdoba (Palma del Río)",
        latitude=37.6997,
        longitude=-5.2814,
        elevation=55.0,
        crop_or_land_type="Herbáceos de regadío",
    ),
    GridNode(
        name="Almería Poniente",
        region="Almería (El Ejido)",
        latitude=36.7763,
        longitude=-2.8146,
        elevation=80.0,
        crop_or_land_type="Horticultura bajo invernadero",
    ),
    GridNode(
        name="Delta del Ebro",
        region="Tarragona (Deltebre)",
        latitude=40.7194,
        longitude=0.7264,
        elevation=8.0,
        crop_or_land_type="Arrozal",
    ),
    GridNode(
        name="La Mancha",
        region="Ciudad Real (Tomelloso)",
        latitude=39.1579,
        longitude=-3.0214,
        elevation=662.0,
        crop_or_land_type="Viñedo de secano",
    ),
    GridNode(
        name="Ribera del Duero",
        region="Valladolid (Peñafiel)",
        latitude=41.5972,
        longitude=-4.1228,
        elevation=754.0,
        crop_or_land_type="Viñedo",
    ),
    GridNode(
        name="Valle del Ebro",
        region="Zaragoza",
        latitude=41.6488,
        longitude=-0.8891,
        elevation=208.0,
        crop_or_land_type="Cultivos extensivos de regadío",
    ),
)
