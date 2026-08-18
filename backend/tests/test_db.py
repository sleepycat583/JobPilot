from pathlib import Path

from app.core.config import Settings, normalize_database_url
from app.db import build_engine


def test_postgres_urls_use_psycopg3_driver() -> None:
    assert normalize_database_url("postgres://user:pass@db/career") == "postgresql+psycopg://user:pass@db/career"
    assert normalize_database_url("postgresql://user:pass@db/career") == "postgresql+psycopg://user:pass@db/career"
    assert normalize_database_url("postgresql+psycopg://user:pass@db/career") == "postgresql+psycopg://user:pass@db/career"


def test_sqlite_engine_keeps_local_driver(tmp_path: Path) -> None:
    engine = build_engine(Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'app.db'}"))
    try:
        assert engine.url.drivername == "sqlite"
        assert engine.url.database is not None
    finally:
        engine.dispose()


def test_postgres_engine_can_be_constructed_without_connecting() -> None:
    engine = build_engine(Settings(_env_file=None, database_url="postgresql://user:pass@db/career"))
    try:
        assert engine.url.drivername == "postgresql+psycopg"
    finally:
        engine.dispose()
