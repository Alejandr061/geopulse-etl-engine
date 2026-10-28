"""Configuración de entorno. Ninguna credencial vive en el código."""

from __future__ import annotations

import logging
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Parámetros de runtime leídos de variables de entorno y de `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "sqlite:///./geopulse.db"
    log_level: str = "INFO"
    open_meteo_base_url: str = "https://api.open-meteo.com/v1/forecast"
    http_timeout_seconds: float = 30.0
    http_max_retries: int = 3


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Devuelve la configuración cacheada del proceso."""
    return Settings()


def normalized_database_url(url: str | None = None) -> str:
    """Adapta la URL de base de datos al driver de SQLAlchemy.

    Acepta el esquema `postgres://` y `postgresql://` que entregan Supabase
    y Heroku, y lo reescribe a `postgresql+psycopg://` para usar psycopg 3.
    SQLite se deja intacto.
    """
    raw = url if url is not None else get_settings().database_url
    if raw.startswith("postgres://"):
        raw = "postgresql://" + raw[len("postgres://") :]
    if raw.startswith("postgresql+psycopg2://"):
        raw = "postgresql+psycopg://" + raw[len("postgresql+psycopg2://") :]
    elif raw.startswith("postgresql://"):
        raw = "postgresql+psycopg://" + raw[len("postgresql://") :]
    return raw


def safe_database_label(url: str | None = None) -> str:
    """Oculta credenciales al escribir la URL en logs."""
    raw = url if url is not None else normalized_database_url()
    if "@" in raw:
        return raw.split("@", 1)[1]
    return raw


def setup_logging() -> None:
    """Configura un log estructurado en una sola línea. Es idempotente."""
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    else:
        root.setLevel(level)
