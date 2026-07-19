"""业务 SQLite Engine 装配。

本模块只创建 SQLAlchemy Engine，并在每个新连接建立时注入 SQLite PRAGMA；
调用方包括 API 生命周期、脚本和测试。LangGraph Checkpointer 继续使用独立连接。
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url


def resolve_sqlite_target(database_url: str) -> Path | None:
    """解析 SQLite URL 对应的本地文件路径。

    参数：
        database_url: SQLAlchemy 数据库 URL。

    返回值：
        若为文件型 SQLite URL，则返回规范化绝对路径；否则返回 `None`。
    """

    url = make_url(database_url)
    if url.drivername != "sqlite":
        return None
    database = url.database
    if database in (None, "", ":memory:"):
        return None
    return Path(database).resolve()


def ensure_database_paths_are_isolated(database_url: str, checkpoint_path: str | Path) -> None:
    """确保业务库与 Checkpoint 文件不会落到同一路径。

    为什么这样做：
        架构文档要求业务查询库与 LangGraph Checkpoint 物理隔离，避免锁竞争与
        职责边界混淆。这里在装配阶段尽早失败，防止配置错误进入运行时。
    """

    database_target = resolve_sqlite_target(database_url)
    checkpoint_target = Path(checkpoint_path).resolve()
    if database_target is not None and database_target == checkpoint_target:
        raise RuntimeError(
            "SQLAlchemy business database must not share the same SQLite file as LangGraph checkpoint"
        )


def create_sqlalchemy_engine(database_url: str) -> Engine:
    """创建业务 SQLite 的 SQLAlchemy Engine。

    参数：
        database_url: 业务数据库 URL，当前阶段固定为 SQLite 文件。

    返回值：
        已注入 SQLite PRAGMA 的 SQLAlchemy Engine。
    """

    engine = create_engine(database_url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _configure_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine