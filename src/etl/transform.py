"""Validación y cálculo vectorizado de indicadores agroclimáticos.

Las fórmulas físicas viven aquí, separadas de la E/S, para poder probarlas
sin red ni base de datos.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)

# Ecuación de Tetens (1930) en la forma adoptada por FAO-56 (Allen et al., 1998).
# es(T) = 0.6108 * exp(17.27 * T / (T + 237.3))  con T en °C y es en kPa.
TETENS_SCALE_KPA = 0.6108
TETENS_SLOPE = 17.27
TETENS_OFFSET_C = 237.3

# Suelo franco de referencia para traducir humedad volumétrica a agotamiento.
FIELD_CAPACITY_M3M3 = 0.35
WILTING_POINT_M3M3 = 0.12
NEUTRAL_SOIL_MOISTURE = 0.25
CLIMATIC_EPSILON_MM = 0.05
CLIMATIC_WEIGHT = 0.40
SOIL_WEIGHT = 0.60

RISK_MODERATE = 25.0
RISK_HIGH = 50.0
RISK_CRITICAL = 75.0
VPD_ANOMALY_KPA = 2.0
HSI_ANOMALY = 75.0

OUTPUT_COLUMNS = [
    "zone_id",
    "timestamp",
    "temp_c",
    "humidity_pct",
    "soil_moisture_m3m3",
    "et0_mm",
    "precipitation_mm",
    "vpd_kpa",
    "hydric_stress_index",
    "risk_level",
    "is_anomaly",
]


class RawWeatherSample(BaseModel):
    """Observación cruda antes del cálculo biofísico."""

    model_config = ConfigDict(extra="ignore")

    zone_id: int = Field(gt=0)
    timestamp: datetime
    temp_c: float | None = None
    humidity_pct: float | None = None
    soil_moisture_m3m3: float | None = None
    et0_mm: float | None = None
    precipitation_mm: float | None = None

    @field_validator("temp_c")
    @classmethod
    def _temperature_is_physical(cls, value: float | None) -> float | None:
        if value is not None and not -45.0 <= value <= 55.0:
            raise ValueError("temperatura fuera del rango físico")
        return value

    @field_validator("humidity_pct")
    @classmethod
    def _humidity_is_physical(cls, value: float | None) -> float | None:
        if value is not None and not 0.0 <= value <= 100.0:
            raise ValueError("humedad relativa fuera de 0-100")
        return value

    @field_validator("soil_moisture_m3m3")
    @classmethod
    def _soil_is_fraction(cls, value: float | None) -> float | None:
        if value is not None and not 0.0 <= value <= 1.0:
            raise ValueError("humedad de suelo fuera de 0-1 m3/m3")
        return value

    @field_validator("et0_mm", "precipitation_mm")
    @classmethod
    def _fluxes_are_non_negative(cls, value: float | None) -> float | None:
        if value is not None and value < 0.0:
            raise ValueError("ET0 o precipitación negativa")
        return value


@dataclass(slots=True)
class TransformResult:
    """Salida tabular del transform y el balance de calidad."""

    frame: pd.DataFrame
    rows_received: int
    rows_discarded: int
    rows_imputed: int


def saturation_vapor_pressure_kpa(temp_c: np.ndarray) -> np.ndarray:
    """Presión de vapor de saturación (kPa) por la ecuación de Tetens.

    es = 0.6108 * exp(17.27 * T / (T + 237.3))
    """
    temperature = np.asarray(temp_c, dtype=float)
    return TETENS_SCALE_KPA * np.exp((TETENS_SLOPE * temperature) / (temperature + TETENS_OFFSET_C))


def compute_vpd_kpa(temp_c: np.ndarray, humidity_pct: np.ndarray) -> np.ndarray:
    """Déficit de presión de vapor (kPa).

    VPD = es(T) * (1 - RH / 100)

    Es la diferencia entre el vapor que el aire podría contener a saturación
    y el que realmente contiene. A RH = 100 % el déficit es nulo.
    """
    humidity = np.clip(np.asarray(humidity_pct, dtype=float), 0.0, 100.0)
    return saturation_vapor_pressure_kpa(temp_c) * (1.0 - humidity / 100.0)


def compute_hydric_stress_index(
    et0_mm: np.ndarray,
    precipitation_mm: np.ndarray,
    soil_moisture_m3m3: np.ndarray,
) -> np.ndarray:
    """Índice de estrés hídrico en el intervalo [0, 100].

    Combina la demanda atmosférica no cubierta por la lluvia y el
    agotamiento de agua en el suelo:

        déficit climático = ET0 / (ET0 + P + ε)
        agotamiento = clip((θ_cc - θ) / (θ_cc - θ_pmp), 0, 1)
        HSI = 100 * (0.40 * déficit climático + 0.60 * agotamiento)

    θ_cc = 0.35 m³/m³ (capacidad de campo) y θ_pmp = 0.12 m³/m³
    (punto de marchitez). ε = 0.05 mm evita la división por cero en horas
    nocturnas, cuando ET0 y la precipitación son nulos: en ese caso el
    déficit climático cae a cero y el índice solo refleja el suelo.
    Un suelo en capacidad de campo aporta 0; en o bajo marchitez aporta 1.
    """
    et0 = np.clip(np.asarray(et0_mm, dtype=float), 0.0, None)
    rain = np.clip(np.asarray(precipitation_mm, dtype=float), 0.0, None)
    theta = np.asarray(soil_moisture_m3m3, dtype=float)

    climatic_ratio = et0 / (et0 + rain + CLIMATIC_EPSILON_MM)
    span = FIELD_CAPACITY_M3M3 - WILTING_POINT_M3M3
    soil_stress = np.clip((FIELD_CAPACITY_M3M3 - theta) / span, 0.0, 1.0)
    index = 100.0 * (CLIMATIC_WEIGHT * climatic_ratio + SOIL_WEIGHT * soil_stress)
    return np.clip(index, 0.0, 100.0)


def classify_risk(hydric_stress_index: np.ndarray) -> np.ndarray:
    """Clasifica el índice de estrés hídrico en cuatro niveles operativos.

    OPTIMAL < 25 ≤ MODERATE < 50 ≤ HIGH_STRESS < 75 ≤ CRITICAL
    """
    stress = np.asarray(hydric_stress_index, dtype=float)
    labels = np.full(stress.shape, "OPTIMAL", dtype=object)
    labels[stress >= RISK_MODERATE] = "MODERATE"
    labels[stress >= RISK_HIGH] = "HIGH_STRESS"
    labels[stress >= RISK_CRITICAL] = "CRITICAL"
    return labels


def detect_anomaly(vpd_kpa: np.ndarray, hydric_stress_index: np.ndarray) -> np.ndarray:
    """Marca la hora como anómala si el VPD o el estrés alcanzan el umbral crítico.

    Umbrales: VPD ≥ 2.0 kPa o índice de estrés hídrico ≥ 75.
    El VPD de 2 kPa es el rango en el que muchos cultivos hortícolas cierran
    estomas de forma severa. El índice 75 coincide con el nivel CRITICAL.
    """
    vpd = np.asarray(vpd_kpa, dtype=float)
    stress = np.asarray(hydric_stress_index, dtype=float)
    return (vpd >= VPD_ANOMALY_KPA) | (stress >= HSI_ANOMALY)


def transform_observations(rows: list[dict[str, object]]) -> TransformResult:
    """Valida, imputa nulos y calcula VPD, estrés, riesgo y anomalía."""
    received = len(rows)
    valid, discarded = _validate(rows)
    if not valid:
        logger.info("transformación vacía | recibidas=%d descartadas=%d", received, discarded)
        return TransformResult(pd.DataFrame(columns=OUTPUT_COLUMNS), received, discarded, 0)

    frame = pd.DataFrame(valid)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    for column in ("temp_c", "humidity_pct", "soil_moisture_m3m3", "et0_mm", "precipitation_mm"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.sort_values(["zone_id", "timestamp"], kind="mergesort").reset_index(drop=True)
    frame = frame.drop_duplicates(subset=["zone_id", "timestamp"], keep="last").reset_index(drop=True)
    frame, imputed = _impute(frame)

    frame["vpd_kpa"] = compute_vpd_kpa(frame["temp_c"].to_numpy(), frame["humidity_pct"].to_numpy())
    frame["hydric_stress_index"] = compute_hydric_stress_index(
        frame["et0_mm"].to_numpy(),
        frame["precipitation_mm"].to_numpy(),
        frame["soil_moisture_m3m3"].to_numpy(),
    )
    frame["risk_level"] = classify_risk(frame["hydric_stress_index"].to_numpy())
    frame["is_anomaly"] = detect_anomaly(
        frame["vpd_kpa"].to_numpy(),
        frame["hydric_stress_index"].to_numpy(),
    )

    frame["temp_c"] = frame["temp_c"].round(2)
    frame["humidity_pct"] = frame["humidity_pct"].round(2)
    frame["soil_moisture_m3m3"] = frame["soil_moisture_m3m3"].round(4)
    frame["et0_mm"] = frame["et0_mm"].round(3)
    frame["precipitation_mm"] = frame["precipitation_mm"].round(3)
    frame["vpd_kpa"] = frame["vpd_kpa"].round(4)
    frame["hydric_stress_index"] = frame["hydric_stress_index"].round(2)
    frame["is_anomaly"] = frame["is_anomaly"].astype(bool)
    frame = frame[OUTPUT_COLUMNS]

    logger.info(
        "transformación lista | recibidas=%d válidas=%d descartadas=%d celdas_imputadas=%d anomalías=%d",
        received,
        len(frame),
        discarded,
        imputed,
        int(frame["is_anomaly"].sum()),
    )
    return TransformResult(frame, received, discarded, imputed)


def _validate(rows: list[dict[str, object]]) -> tuple[list[dict[str, object]], int]:
    valid: list[dict[str, object]] = []
    discarded = 0
    for row in rows:
        try:
            sample = RawWeatherSample.model_validate(row)
        except ValidationError:
            discarded += 1
            continue
        if sample.temp_c is None or sample.humidity_pct is None:
            discarded += 1
            continue
        valid.append(sample.model_dump())
    if discarded:
        logger.warning("observaciones descartadas por calidad | n=%d", discarded)
    return valid, discarded


def _impute(frame: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Imputa huecos sin inventar lluvia.

    La precipitación ausente pasa a 0 mm: interpolarla fabricaría lluvia.
    ET0 y la humedad del suelo se interpolan por tiempo dentro de cada zona;
    si la serie entera está vacía, se usa 0 mm y 0.25 m³/m³.
    """
    imputed = 0
    missing_rain = frame["precipitation_mm"].isna()
    imputed += int(missing_rain.sum())
    frame.loc[missing_rain, "precipitation_mm"] = 0.0

    pieces: list[pd.DataFrame] = []
    for _, group in frame.groupby("zone_id", sort=False):
        group = group.copy()
        indexed = group.set_index("timestamp")
        for column, fallback in (("et0_mm", 0.0), ("soil_moisture_m3m3", NEUTRAL_SOIL_MOISTURE)):
            missing = group[column].isna()
            imputed += int(missing.sum())
            filled = indexed[column].interpolate(method="time", limit_direction="both")
            group[column] = filled.to_numpy()
            group[column] = group[column].fillna(fallback)
        pieces.append(group)
    if not pieces:
        return frame, imputed
    return pd.concat(pieces, ignore_index=True), imputed
