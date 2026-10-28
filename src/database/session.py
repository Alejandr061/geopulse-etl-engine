"""Motor SQLAlchemy con pool y sesión perezosa.

SQLite es el destino local. Si `DATABASE_URL` apunta a PostgreSQL, el motor
abre un pool de conexiones (`pool_size=5`, `max_overflow=10`) con
`pool_pre_ping` para sobrevivir a reinicios del servidor.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.config import normalized_database_url
from src.database.models import Base

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """Crea el motor una sola vez por proceso."""
    global _engine
    if _engine is None:
        url = normalized_database_url()
        kwargs: dict[str, object] = {"pool_pre_ping": True, "future": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            kwargs["pool_size"] = 5
            kwargs["max_overflow"] = 10
        _engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):
            _enable_sqlite_foreign_keys(_engine)
    return _engine


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def get_session_factory() -> sessionmaker[Session]:
    """Fábrica de sesiones ligada al motor actual."""
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(),
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
    return _session_factory


def reset_engine() -> None:
    """Cierra el motor cacheado. Lo usan los tests con bases temporales."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


@contextmanager
def session_scope() -> Iterator[Session]:
    """Sesión con commit al salir y rollback si hay error."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Crea el esquema si no existe y asegura la malla de zonas."""
    from src.database.seed import seed_monitoring_zones

    Base.metadata.create_all(get_engine())
    seed_monitoring_zones()
