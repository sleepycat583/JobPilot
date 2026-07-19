"""Alembic 迁移链集成测试。"""

from __future__ import annotations

import shutil
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
    assert revision == "20260720_0002"
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

    assert revision == "20260720_0002"


@pytest.mark.core_agent_tests
def test_alembic_failed_follow_up_upgrade_preserves_existing_business_rows(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已有业务数据在后续迁移失败后必须保持完整且版本不能静默推进。"""

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
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO experiment_runs (
                        case_name, architecture, run_index, model_name, prompt_version,
                        status, schema_valid, unsupported_skill_claims, llm_calls,
                        estimated_tokens, latency_ms, error_codes, output_json, created_at
                    ) VALUES (
                        'existing-case', 'baseline', 99, 'test-model', 'v-existing',
                        'success', 1, 0, 1, 1, 1.5, '[]', '{}', '2026-07-20T00:00:00+00:00'
                    )
                    """
                )
            )
    finally:
        engine.dispose()

    broken_migrations = tmp_path / "broken_migrations"
    shutil.copytree(project_root / "migrations", broken_migrations)
    (broken_migrations / "versions" / "20260720_0003_failure_probe.py").write_text(
        """\"\"\"Temporary migration failure probe.\"\"\"

revision = "20260720_0003"
down_revision = "20260720_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    raise RuntimeError("INTENTIONAL_MIGRATION_FAILURE")


def downgrade() -> None:
    pass
""",
        encoding="utf-8",
    )
    broken_config = _build_alembic_config(project_root, database_path)
    broken_config.set_main_option("script_location", str(broken_migrations))

    with pytest.raises(RuntimeError, match="INTENTIONAL_MIGRATION_FAILURE"):
        command.upgrade(broken_config, "head")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT case_name, run_index, prompt_version FROM experiment_runs WHERE case_name = 'existing-case'"
                )
            ).one()
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    finally:
        engine.dispose()

    assert row == ("existing-case", 99, "v-existing")
    assert revision == "20260720_0002"
    assert not checkpoint_path.exists()


@pytest.mark.core_agent_tests
def test_alembic_unique_constraint_preserves_clean_existing_rows(
    project_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """batch migration 应保留无冲突的既有行。"""

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
    command.upgrade(config, "20260719_0001")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO experiment_runs (case_name,architecture,run_index,model_name,prompt_version,status,schema_valid,llm_calls,estimated_tokens,latency_ms,error_codes,created_at) VALUES ('case1','baseline',1,'model','v1','success',1,1,1,1.0,'[]','2026-01-01T00:00:00+00:00')"))
    finally:
        engine.dispose()
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT COUNT(*) FROM experiment_runs")).scalar_one() == 1
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260720_0002"
    finally:
        engine.dispose()


@pytest.mark.core_agent_tests
def test_alembic_unique_constraint_failure_preserves_duplicate_source_rows(
    project_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """batch migration 遇到历史重复五元组时应失败且保留原表数据。"""

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
    command.upgrade(config, "20260719_0001")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.begin() as connection:
            for _ in range(2):
                connection.execute(text("INSERT INTO experiment_runs (case_name,architecture,run_index,model_name,prompt_version,status,schema_valid,llm_calls,estimated_tokens,latency_ms,error_codes,created_at) VALUES ('case1','baseline',1,'model','v1','success',1,1,1,1.0,'[]','2026-01-01T00:00:00+00:00')"))
    finally:
        engine.dispose()
    with pytest.raises(Exception):
        command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT COUNT(*) FROM experiment_runs")).scalar_one() == 2
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260719_0001"
    finally:
        engine.dispose()