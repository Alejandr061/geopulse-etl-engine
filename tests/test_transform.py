"""Fórmulas de VPD (Tetens) y del índice de estrés hídrico."""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest

from src.etl.transform import (
    CLIMATIC_EPSILON_MM,
    CLIMATIC_WEIGHT,
    FIELD_CAPACITY_M3M3,
    NEUTRAL_SOIL_MOISTURE,
    SOIL_WEIGHT,
    WILTING_POINT_M3M3,
    classify_risk,
    compute_hydric_stress_index,
    compute_vpd_kpa,
    detect_anomaly,
    saturation_vapor_pressure_kpa,
    transform_observations,
)


def _sample(
    hour: int,
    *,
    temp: float = 24.0,
    humidity: float | None = 45.0,
    soil: float | None = 0.28,
    et0: float | None = 0.25,
    rain: float | None = 0.0,
    zone_id: int = 1,
) -> dict[str, object]:
    return {
        "zone_id": zone_id,
        "timestamp": datetime(2026, 10, 28, hour),
        "temp_c": temp,
        "humidity_pct": humidity,
        "soil_moisture_m3m3": soil,
        "et0_mm": et0,
        "precipitation_mm": rain,
    }


def test_saturation_vapor_pressure_matches_fao56_at_25c() -> None:
    """FAO-56 tabula es(25 °C) = 3.167 kPa con la forma de Tetens."""
    pressure = float(saturation_vapor_pressure_kpa(np.array([25.0]))[0])
    assert pressure == pytest.approx(3.167, abs=0.001)


def test_vpd_tetens_half_humidity() -> None:
    """A 25 °C y 50 % de HR el déficit es la mitad de la presión de saturación."""
    vpd = float(compute_vpd_kpa(np.array([25.0]), np.array([50.0]))[0])
    assert vpd == pytest.approx(1.5835, abs=0.001)


def test_vpd_is_zero_when_air_is_saturated() -> None:
    vpd = float(compute_vpd_kpa(np.array([18.0]), np.array([100.0]))[0])
    assert vpd == pytest.approx(0.0, abs=1e-12)


def test_vpd_rises_with_temperature_and_falls_with_humidity() -> None:
    warmer = compute_vpd_kpa(np.array([20.0, 30.0]), np.array([40.0, 40.0]))
    wetter = compute_vpd_kpa(np.array([25.0, 25.0]), np.array([30.0, 80.0]))
    assert warmer[1] > warmer[0]
    assert wetter[1] < wetter[0]


def test_hydric_stress_reference_composition() -> None:
    """HSI = 100 * (0.40 * ET0/(ET0+P+ε) + 0.60 * agotamiento de suelo)."""
    et0 = np.array([0.30])
    rain = np.array([0.10])
    soil = np.array([0.20])
    got = float(compute_hydric_stress_index(et0, rain, soil)[0])

    climatic = 0.30 / (0.30 + 0.10 + CLIMATIC_EPSILON_MM)
    depletion = (FIELD_CAPACITY_M3M3 - 0.20) / (FIELD_CAPACITY_M3M3 - WILTING_POINT_M3M3)
    expected = 100.0 * (CLIMATIC_WEIGHT * climatic + SOIL_WEIGHT * depletion)

    assert got == pytest.approx(expected)
    assert got == pytest.approx(65.7971, abs=0.001)


def test_hydric_stress_extremes() -> None:
    wet = float(compute_hydric_stress_index(np.array([0.05]), np.array([4.0]), np.array([0.40]))[0])
    dry = float(compute_hydric_stress_index(np.array([0.45]), np.array([0.0]), np.array([0.10]))[0])
    assert wet < 15.0
    assert dry > 80.0
    assert 0.0 <= wet <= 100.0
    assert 0.0 <= dry <= 100.0


def test_risk_bins_follow_the_operational_thresholds() -> None:
    labels = classify_risk(np.array([0.0, 24.99, 25.0, 49.99, 50.0, 74.99, 75.0, 100.0]))
    assert labels.tolist() == [
        "OPTIMAL",
        "OPTIMAL",
        "MODERATE",
        "MODERATE",
        "HIGH_STRESS",
        "HIGH_STRESS",
        "CRITICAL",
        "CRITICAL",
    ]


def test_anomaly_flags_critical_vpd_or_critical_stress() -> None:
    flags = detect_anomaly(
        np.array([1.99, 2.0, 0.4]),
        np.array([74.99, 10.0, 75.0]),
    )
    assert flags.tolist() == [False, True, True]


def test_transform_discards_incomplete_rows_and_imputes_soil_and_rain() -> None:
    rows = [
        _sample(12, humidity=None),
        _sample(13, temp=80.0),
        _sample(14, soil=None, rain=None, humidity=40.0, temp=23.0),
    ]
    result = transform_observations(rows)

    assert result.rows_received == 3
    assert result.rows_discarded == 2
    assert result.rows_imputed == 2
    assert len(result.frame) == 1
    row = result.frame.iloc[0]
    assert row["soil_moisture_m3m3"] == pytest.approx(NEUTRAL_SOIL_MOISTURE)
    assert row["precipitation_mm"] == pytest.approx(0.0)
    assert row["vpd_kpa"] > 0


def test_transform_interpolates_soil_and_et0_but_not_rainfall() -> None:
    rows = [
        _sample(12, soil=0.20, et0=0.20, rain=0.0),
        _sample(13, soil=None, et0=None, rain=None),
        _sample(14, soil=0.40, et0=0.40, rain=1.0),
    ]
    result = transform_observations(rows)
    middle = result.frame.iloc[1]

    assert middle["soil_moisture_m3m3"] == pytest.approx(0.30)
    assert middle["et0_mm"] == pytest.approx(0.30)
    assert middle["precipitation_mm"] == pytest.approx(0.0)
    assert result.rows_imputed == 3
    assert set(result.frame["risk_level"]).issubset({"OPTIMAL", "MODERATE", "HIGH_STRESS", "CRITICAL"})
