from collections.abc import AsyncIterator
from contextlib import AbstractContextManager, asynccontextmanager
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


@asynccontextmanager
async def open_async_checkpoint(settings: Settings) -> AsyncIterator[BaseCheckpointSaver]:
    """为异步 LangGraph 流创建匹配的 checkpoint saver。

    `graph.astream()` 会调用 checkpoint saver 的异步方法。SQLite 必须使用
    `AsyncSqliteSaver`，否则图刚启动就会因同步 saver 不支持 `aget_tuple()` 而失败。
    PostgreSQL 路径使用 `AsyncPostgresSaver`，保证不同数据库后端的异步行为一致。
    """

    if settings.checkpoint_backend == "sqlite":
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        settings.graph_checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        async with AsyncSqliteSaver.from_conn_string(str(settings.graph_checkpoint_path)) as saver:
            yield saver
        return

    database_url = (settings.graph_checkpoint_database_url or "").strip()
    if not database_url:
        raise RuntimeError(
            "GRAPH_CHECKPOINT_DATABASE_URL is required when CHECKPOINT_BACKEND=postgres"
        )

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(database_url) as saver:
        yield saver


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


async def initialize_checkpoint_async(
    checkpointer: BaseCheckpointSaver,
    settings: Settings,
) -> None:
    """为异步 checkpoint saver 创建 PostgreSQL 表，仅在显式配置时执行。"""

    if settings.checkpoint_backend != "postgres" or not settings.auto_create_checkpoint_schema:
        return
    setup = getattr(checkpointer, "setup", None)
    if not callable(setup):
        raise RuntimeError("Configured PostgreSQL checkpoint saver does not support setup()")
    result = setup()
    if hasattr(result, "__await__"):
        await result


def probe_checkpoint(checkpointer: BaseCheckpointSaver) -> None:
    """Run a read-only checkpoint query for readiness checks.

    The synthetic thread ID is never written. A missing Postgres checkpoint
    schema or an unavailable database surfaces here before traffic reaches the
    conversation graph.
    """

    checkpointer.get_tuple(
        {"configurable": {"thread_id": "__career_workbench_healthcheck__", "checkpoint_ns": ""}}
    )


async def probe_checkpoint_async(checkpointer: BaseCheckpointSaver) -> None:
    """异步检查 checkpoint 存储是否可读，且不会写入健康检查数据。"""

    async_probe = getattr(checkpointer, "aget_tuple", None)
    if callable(async_probe):
        await async_probe(
            {"configurable": {"thread_id": "__career_workbench_healthcheck__", "checkpoint_ns": ""}}
        )
        return
    probe_checkpoint(checkpointer)
