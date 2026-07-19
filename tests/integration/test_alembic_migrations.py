"""Alembic 迁移链集成测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _build_alembic_config(project_root: Path, database_path: Path) -> Config:
    """创建指向临时业务库的 Alembic 配置。"""

    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    return config


@pytest.mark.core_agent_tests
def test_alembic_upgrade_creates_business_tables(project_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`upgrade head` 应只初始化业务表，不触碰 Checkpoint 文件。"""

    database_path = tmp_path / "app.sqlite3"
    checkpoint_path = tmp_path / "checkpoints.sqlite3"
    monkeypatch.setenv("MODEL_PROVIDER", "openai_compatible")
    monkeypatch.setenv("BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("MODEL_NAME", "deepseek-chat")
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("CHROMA_PERSIST_DIR", "./data/chroma")
    monkeypatch.setenv("SQLALCHEMY_DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    monkeypatch.setenv("LANGGRAPH_CHECKPOINT_PATH", str(checkpoint_path))
    monkeypatch.setenv("EMBEDDING_DEVICE", "cpu")

    config = _build_alembic_config(project_root, database_path)
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        revision = engine.connect().execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    finally:
        engine.dispose()

    assert "experiment_runs" in tables
    assert "review_audits" in tables
    assert revision == "20260719_0001"
    assert not checkpoint_path.exists()


@pytest.mark.core_agent_tests
def test_alembic_upgrade_is_repeatable_for_existing_business_database(project_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """对同一临时业务库重复执行 `upgrade head` 应保持成功。"""

    database_path = tmp_path / "app.sqlite3"
    monkeypatch.setenv("MODEL_PROVIDER", "openai_compatible")
    monkeypatch.setenv("BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("MODEL_NAME", "deepseek-chat")
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("CHROMA_PERSIST_DIR", "./data/chroma")
    monkeypatch.setenv("SQLALCHEMY_DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    monkeypatch.setenv("LANGGRAPH_CHECKPOINT_PATH", str(tmp_path / "checkpoints.sqlite3"))
    monkeypatch.setenv("EMBEDDING_DEVICE", "cpu")

    config = _build_alembic_config(project_root, database_path)
    command.upgrade(config, "head")
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        revision = engine.connect().execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    finally:
        engine.dispose()

    assert revision == "20260719_0001"