from pathlib import Path

import pytest

from app.core.checkpoint import initialize_checkpoint, open_checkpoint, probe_checkpoint
from app.core.config import Settings


def test_sqlite_checkpoint_is_selected_by_default(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, graph_checkpoint_path=tmp_path / "graph.db")

    manager = open_checkpoint(settings)
    checkpointer = manager.__enter__()
    try:
        assert checkpointer.__class__.__name__ == "SqliteSaver"
        assert (tmp_path / "graph.db").exists()
    finally:
        manager.__exit__(None, None, None)


def test_postgres_checkpoint_requires_explicit_url() -> None:
    settings = Settings(_env_file=None, checkpoint_backend="postgres")

    with pytest.raises(RuntimeError, match="GRAPH_CHECKPOINT_DATABASE_URL"):
        open_checkpoint(settings)


def test_postgres_schema_creation_is_opt_in() -> None:
    calls: list[str] = []

    class FakeSaver:
        def setup(self) -> None:
            calls.append("setup")

    settings = Settings(
        _env_file=None,
        checkpoint_backend="postgres",
        auto_create_checkpoint_schema=True,
    )
    initialize_checkpoint(FakeSaver(), settings)  # type: ignore[arg-type]
    assert calls == ["setup"]


def test_sqlite_does_not_call_postgres_setup() -> None:
    calls: list[str] = []

    class FakeSaver:
        def setup(self) -> None:
            calls.append("setup")

    settings = Settings(_env_file=None, checkpoint_backend="sqlite", auto_create_checkpoint_schema=True)
    initialize_checkpoint(FakeSaver(), settings)  # type: ignore[arg-type]
    assert calls == []


def test_checkpoint_probe_is_read_only(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, graph_checkpoint_path=tmp_path / "graph.db")
    manager = open_checkpoint(settings)
    checkpointer = manager.__enter__()
    try:
        probe_checkpoint(checkpointer)
        assert not list(checkpointer.list({"configurable": {"thread_id": "__career_workbench_healthcheck__"}}))
    finally:
        manager.__exit__(None, None, None)
