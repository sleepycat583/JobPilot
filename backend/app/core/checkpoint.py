from collections.abc import Iterator
from contextlib import AbstractContextManager
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver

from app.core.config import Settings


def open_checkpoint(settings: Settings) -> AbstractContextManager[BaseCheckpointSaver]:
    """Create the explicitly selected checkpoint backend.

    The backend is intentionally fail-fast: a PostgreSQL configuration error must
    not silently fall back to a local SQLite file in production.
    """

    if settings.checkpoint_backend == "sqlite":
        settings.graph_checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        return SqliteSaver.from_conn_string(str(settings.graph_checkpoint_path))

    database_url = (settings.graph_checkpoint_database_url or "").strip()
    if not database_url:
        raise RuntimeError(
            "GRAPH_CHECKPOINT_DATABASE_URL is required when CHECKPOINT_BACKEND=postgres"
        )

    from langgraph.checkpoint.postgres import PostgresSaver

    return PostgresSaver.from_conn_string(database_url)


def initialize_checkpoint(
    checkpointer: BaseCheckpointSaver,
    settings: Settings,
) -> None:
    """Create Postgres checkpoint tables only when explicitly requested."""

    if settings.checkpoint_backend == "postgres" and settings.auto_create_checkpoint_schema:
        setup = getattr(checkpointer, "setup", None)
        if not callable(setup):
            raise RuntimeError("Configured PostgreSQL checkpoint saver does not support setup()")
        setup()
