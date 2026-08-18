"""Check PostgreSQL business database connectivity and migrated tables safely."""

from __future__ import annotations

from pathlib import Path
import sys

from sqlalchemy import inspect, text


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import get_settings, normalize_database_url  # noqa: E402
from app.db import build_engine  # noqa: E402


REQUIRED_TABLES = {"jobs", "resumes", "job_descriptions", "threads", "execution_events", "idempotency_records"}


def main() -> int:
    settings = get_settings()
    database_url = normalize_database_url(settings.database_url)
    if not database_url.startswith("postgresql+psycopg://"):
        print("FAIL DATABASE_URL must target PostgreSQL with the Psycopg 3 driver", file=sys.stderr)
        return 1

    engine = None
    try:
        engine = build_engine(settings)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        tables = set(inspect(engine).get_table_names())
        missing = REQUIRED_TABLES - tables
        if missing:
            print("FAIL PostgreSQL is reachable but Alembic schema is incomplete", file=sys.stderr)
            return 1
        print("PASS PostgreSQL business database connectivity and schema")
        return 0
    except Exception:
        # Do not print the exception: it may contain credentials or host details.
        print("FAIL PostgreSQL business database operation", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
