from pathlib import Path

import pytest

from app.core.checkpoint import initialize_checkpoint, open_async_checkpoint, open_checkpoint, probe_checkpoint_async
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
        checkpointer.get_tuple({"configurable": {"thread_id": "__career_workbench_healthcheck__", "checkpoint_ns": ""}})
        assert not list(checkpointer.list({"configurable": {"thread_id": "__career_workbench_healthcheck__"}}))
    finally:
        manager.__exit__(None, None, None)


def test_async_sqlite_checkpoint_supports_async_probe(tmp_path: Path) -> None:
    import asyncio

    settings = Settings(_env_file=None, graph_checkpoint_path=tmp_path / "async-graph.db")

    async def check() -> None:
        async with open_async_checkpoint(settings) as checkpointer:
            await probe_checkpoint_async(checkpointer)
            assert checkpointer.__class__.__name__ == "AsyncSqliteSaver"

    asyncio.run(check())


def test_async_sqlite_checkpoint_supports_graph_stream(tmp_path: Path) -> None:
    import asyncio
    from langchain_core.messages import HumanMessage

    from app.core.observability import build_trace_config
    from app.graph import build_career_graph
    from app.graph.models import ModelBundle, StubSupervisorModel

    settings = Settings(_env_file=None, graph_checkpoint_path=tmp_path / "stream-graph.db")

    async def check() -> None:
        async with open_async_checkpoint(settings) as checkpointer:
            graph = build_career_graph(
                ModelBundle(mode="stub", supervisor_model=StubSupervisorModel(), worker_model=None),
                checkpointer,
            )
            chunks = [
                chunk
                async for chunk in graph.astream(
                    {
                        "messages": [HumanMessage(content="测试异步流")],
                        "remaining_steps": 30,
                        "readonly_context": {"latest_user_message": "测试异步流"},
                        "run_id": "async-stream-test",
                        "route_audit": None,
                        "worker_name": None,
                        "worker_result": None,
                        "visible_output": None,
                        "public_output": None,
                    },
                    build_trace_config("async-stream-thread", "async-stream-test", operation="test"),
                    stream_mode="values",
                )
            ]
            assert chunks
            assert chunks[-1]["public_output"]

    asyncio.run(check())
