"""Parser de Open-Meteo, sin red."""

from __future__ import annotations

import pytest

from src.etl.extract import ExtractError, ZoneTarget, parse_forecast_payload


def _zone(zone_id: int, latitude: float, longitude: float, name: str) -> ZoneTarget:
    return ZoneTarget(
        id=zone_id,
        name=name,
        region="Test",
        latitude=latitude,
        longitude=longitude,
        crop_or_land_type="Test",
    )


def test_current_reading_replaces_the_matching_hour() -> None:
    zone = _zone(1, 39.94, -0.10, "Plana Baixa")
    payload = {
        "latitude": 39.94,
        "longitude": -0.10,
        "hourly": {
            "time": ["2026-10-28T15:00", "2026-10-28T16:00"],
            "temperature_2m": [20.0, 21.0],
            "relative_humidity_2m": [50, 48],
            "precipitation": [0.0, 0.0],
            "et0_fao_evapotranspiration": [0.2, 0.3],
            "soil_moisture_0_to_7cm": [0.30, None],
        },
        "current": {
            "time": "2026-10-28T15:00",
            "temperature_2m": 21.5,
            "relative_humidity_2m": 47,
            "precipitation": 0.1,
            "et0_fao_evapotranspiration": 0.22,
            "soil_moisture_0_to_7cm": 0.31,
        },
    }

    rows = parse_forecast_payload(payload, [zone])

    assert len(rows) == 2
    current = next(row for row in rows if row["timestamp"].hour == 15)
    assert current["temp_c"] == 21.5
    assert current["kind"] == "current"
    assert current["soil_moisture_m3m3"] == 0.31
    later = next(row for row in rows if row["timestamp"].hour == 16)
    assert later["soil_moisture_m3m3"] is None


def test_multi_location_payload_keeps_request_order() -> None:
    zones = [
        _zone(1, 39.94, -0.10, "Plana Baixa"),
        _zone(2, 40.72, 0.73, "Delta del Ebro"),
    ]
    payload = [
        {
            "latitude": 39.94,
            "longitude": -0.10,
            "hourly": {
                "time": ["2026-10-28T12:00"],
                "temperature_2m": [19.0],
                "relative_humidity_2m": [60],
                "precipitation": [0.0],
                "et0_fao_evapotranspiration": [0.1],
                "soil_moisture_0_to_7cm": [0.2],
            },
        },
        {
            "latitude": 40.72,
            "longitude": 0.73,
            "hourly": {
                "time": ["2026-10-28T12:00"],
                "temperature_2m": [17.0],
                "relative_humidity_2m": [70],
                "precipitation": [0.4],
                "et0_fao_evapotranspiration": [0.05],
                "soil_moisture_0_to_7cm": [0.4],
            },
        },
    ]

    rows = parse_forecast_payload(payload, zones)
    by_zone = {row["zone_id"]: row for row in rows}
    assert by_zone[1]["temp_c"] == 19.0
    assert by_zone[2]["precipitation_mm"] == 0.4


def test_api_error_payload_raises() -> None:
    with pytest.raises(ExtractError):
        parse_forecast_payload({"error": True, "reason": "latitude out of range"}, [])
