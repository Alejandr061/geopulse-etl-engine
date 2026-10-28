# GeoPulse ETL

**Motor geoespacial de inteligencia agroclimática y ambiental en tiempo real.**

Pipeline ETL sobre Open-Meteo que calcula déficit de presión de vapor y estrés hídrico en ocho comarcas reales de España, los persiste de forma idempotente y los sirve como GeoJSON sobre un centro de control en mapa.

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0+-D71F00?logo=sqlalchemy&logoColor=white)](https://www.sqlalchemy.org/)
[![Open-Meteo](https://img.shields.io/badge/Open--Meteo-Forecast%20API-2F80ED)](https://open-meteo.com/)
[![GitHub Actions](https://github.com/Alejandr061/geopulse-etl-engine/actions/workflows/etl_pipeline.yml/badge.svg)](https://github.com/Alejandr061/geopulse-etl-engine/actions/workflows/etl_pipeline.yml)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

<p align="center">
  <img src="assets/dashboard_preview.png" alt="Centro de control GeoPulse: mapa de riesgo del arco mediterráneo y serie de VPD, humedad del suelo y estrés hídrico en Plana Baixa" width="100%" />
</p>
<p align="center">
  <img src="assets/api_swagger.png" alt="OpenAPI de GeoPulse ETL: trigger del pipeline, GeoJSON latest y serie temporal por zona" width="100%" />
</p>

## Arquitectura

```mermaid
flowchart LR
    A["Open-Meteo API"] --> B["Async Extractor\n(httpx)"]
    B --> C["Vectorized Transformer\n(Pandas/NumPy: VPD & Hydric Stress)"]
    C --> D["Idempotent Loader\n(SQLAlchemy Upsert)"]
    D --> E["FastAPI GeoJSON\n& Leaflet Dashboard"]
```

| Etapa | Módulo | Qué hace |
| --- | --- | --- |
| Extract | `src/etl/extract.py` | Cliente `httpx` asíncrono. Una sola petición para las ocho coordenadas, con reintentos, respeto de `Retry-After` y registro del tiempo de respuesta. |
| Transform | `src/etl/transform.py` | Validación Pydantic. Descarta temperatura o humedad ausentes, imputa lluvia a 0 mm e interpola ET₀ y humedad de suelo. Calcula VPD, estrés, riesgo y anomalía en vector. |
| Load | `src/etl/load.py` | `INSERT … ON CONFLICT (zone_id, timestamp) DO UPDATE`. La segunda corrida actualiza la misma hora. El log deja insertadas, actualizadas y duración del pipeline en milisegundos. |
| Serve | `src/api/main.py` | `POST /api/v1/etl/trigger`, GeoJSON `FeatureCollection` y serie de 48 h atrás + 24 h de pronóstico. El dashboard oscuro consume esos endpoints. |

La base por defecto es SQLite (`sqlite:///./geopulse.db`). `DATABASE_URL` acepta una URL de PostgreSQL o Supabase (`postgres://` y `postgresql://` se normalizan a `postgresql+psycopg://`).

## Modelo físico

### Déficit de presión de vapor (Tetens / FAO-56)

La presión de vapor de saturación, en kPa, con la temperatura del aire `T` en °C:

```text
es(T) = 0.6108 · exp(17.27 · T / (T + 237.3))
VPD   = es(T) · (1 − RH / 100)
```

Aire saturado (`RH = 100`) produce VPD nulo. El déficit crece con la temperatura y cae con la humedad relativa. Umbral de anomalía: **VPD ≥ 2.0 kPa**.

### Índice de estrés hídrico

Combina la demanda atmosférica no cubierta por la lluvia y el agotamiento de agua en el suelo. `ET₀` y la precipitación están en mm de la hora; `θ` es la humedad volumétrica en m³/m³.

```text
déficit climático = ET₀ / (ET₀ + P + 0.05)
agotamiento       = clip((0.35 − θ) / (0.35 − 0.12), 0, 1)
HSI               = 100 · (0.40 · déficit climático + 0.60 · agotamiento)
```

`0.35` es la capacidad de campo y `0.12` el punto de marchitez de un suelo franco de referencia. `0.05 mm` evita dividir por cero de noche, cuando ET₀ y la lluvia son nulos: en ese caso el índice solo refleja el suelo. El resultado queda acotado a `[0, 100]`.

| HSI | `risk_level` |
| --- | --- |
| `< 25` | `OPTIMAL` |
| `25 – 49.9` | `MODERATE` |
| `50 – 74.9` | `HIGH_STRESS` |
| `≥ 75` | `CRITICAL` |

Una hora es anomalía si el VPD o el HSI alcanzan su umbral crítico.

## Malla de monitoreo

Ocho nodos WGS84 anclados a municipios reales:

| Zona | Ancla | Cultivo |
| --- | --- | --- |
| Plana Baixa | Vila-real, Castellón | Cítricos |
| Huerta de Murcia | Murcia | Hortalizas de regadío |
| Vega del Guadalquivir | Palma del Río, Córdoba | Herbáceos de regadío |
| Almería Poniente | El Ejido, Almería | Horticultura bajo invernadero |
| Delta del Ebro | Deltebre, Tarragona | Arrozal |
| La Mancha | Tomelloso, Ciudad Real | Viñedo de secano |
| Ribera del Duero | Peñafiel, Valladolid | Viñedo |
| Valle del Ebro | Zaragoza | Cultivos extensivos de regadío |

## Puesta en marcha

Requiere Python 3.11 o superior.

```bash
python -m venv .venv
```

Windows (PowerShell):

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python run_etl.py
uvicorn src.api.main:app --host 127.0.0.1 --port 8000
```

macOS y Linux:

```bash
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python run_etl.py
uvicorn src.api.main:app --host 127.0.0.1 --port 8000
```

- Centro de control: [http://127.0.0.1:8000](http://127.0.0.1:8000)
- OpenAPI: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

`.env` solo necesita `DATABASE_URL`. No hay credenciales de Open-Meteo.

### Docker

```bash
docker compose up --build
```

La API queda en el puerto `8000` y la base SQLite vive en el volumen `geopulse-data`. Para PostgreSQL:

```bash
docker compose --profile postgres up -d
```

y apunta `DATABASE_URL` a `postgresql://geopulse:geopulse@postgres:5432/geopulse`.

### Tests

```bash
pytest
```

Cubren la ecuación de Tetens, la composición del índice de estrés, los cortes de riesgo, el descarte e imputación de nulos, el parser de Open-Meteo y el upsert idempotente sobre una SQLite temporal.

### Automatización

`.github/workflows/etl_pipeline.yml` ejecuta `pytest` y `python run_etl.py` cada 6 horas (`cron: 0 */6 * * *`) y bajo demanda con `workflow_dispatch`.

## API

| Método | Ruta | Respuesta |
| --- | --- | --- |
| `POST` | `/api/v1/etl/trigger` | Métricas de la corrida: filas insertadas, actualizadas y `elapsed_ms`. |
| `GET` | `/api/v1/geojson/latest` | `FeatureCollection`. Geometría `Point` en `[lon, lat]` y métricas en `properties`. |
| `GET` | `/api/v1/zones/{zone_id}/timeseries` | Historia reciente y pronóstico de 24 h para la gráfica. |

Los timestamps de la base están en UTC.

## Datos

Series horarias y estado actual de [Open-Meteo](https://open-meteo.com/): `temperature_2m`, `relative_humidity_2m`, `precipitation`, `et0_fao_evapotranspiration` y `soil_moisture_0_to_7cm`. El mapa usa Leaflet y el basemap oscuro de Esri. CartoDB Dark Matter exige API key y devuelve un mosaico vacío.

## Licencia

[MIT](LICENSE) © 2026 Alejandro Hidalgo. Puedes usar, modificar y distribuir el proyecto conservando el aviso de copyright.
