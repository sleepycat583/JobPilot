"""LangGraph SQLite Checkpoint 装配。

由 FastAPI 生命周期和跨进程恢复测试调用；本模块不读取业务 SQLite。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver


def open_sqlite_checkpointer(path: str | Path) -> tuple[SqliteSaver, sqlite3.Connection]:
    """打开指定路径的 Checkpoint 数据库并返回 Saver 与其连接。

    参数：path 为独立的 Checkpoint SQLite 文件路径。
    返回：用于编译 Graph 的 SqliteSaver 和调用方负责关闭的 SQLite 连接。
    """

    checkpoint_path = Path(path)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(checkpoint_path, check_same_thread=False)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA busy_timeout=5000")
    return SqliteSaver(connection), connection