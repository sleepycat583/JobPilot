"""业务 SQLite Engine 与 ORM 基础设施测试。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db import Base, ExperimentRun, create_sqlalchemy_engine, ensure_database_paths_are_isolated
from app.db.session import build_session_factory


@pytest.mark.core_agent_tests
def test_business_engine_applies_required_sqlite_pragmas(tmp_path: Path) -> None:
    """业务 Engine 建立的新连接应自动应用架构要求的 SQLite PRAGMA。"""

    database_url = f"sqlite:///{(tmp_path / 'app.sqlite3').as_posix()}"
    engine = create_sqlalchemy_engine(database_url)
    try:
        with engine.connect() as connection:
            journal_mode = connection.execute(text("PRAGMA journal_mode")).scalar_one()
            synchronous = connection.execute(text("PRAGMA synchronous")).scalar_one()
            busy_timeout = connection.execute(text("PRAGMA busy_timeout")).scalar_one()
            foreign_keys = connection.execute(text("PRAGMA foreign_keys")).scalar_one()
    finally:
        engine.dispose()

    assert str(journal_mode).lower() == "wal"
    assert int(synchronous) == 1
    assert int(busy_timeout) == 5000
    assert int(foreign_keys) == 1


@pytest.mark.core_agent_tests
def test_database_and_checkpoint_paths_must_be_isolated(tmp_path: Path) -> None:
    """业务库与 Checkpoint 误配到同一路径时，装配阶段应立即失败。"""

    shared_path = tmp_path / "shared.sqlite3"
    with pytest.raises(RuntimeError, match="must not share"):
        ensure_database_paths_are_isolated(f"sqlite:///{shared_path.as_posix()}", shared_path)


@pytest.mark.core_agent_tests
def test_session_factory_persists_business_models_without_create_all_side_effects(tmp_path: Path) -> None:
    """业务 Session 工厂应能在显式建表后持久化 ORM 实体。"""

    database_path = tmp_path / "app.sqlite3"
    engine = create_sqlalchemy_engine(f"sqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(engine)
    session_factory = build_session_factory(engine)

    try:
        with session_factory() as session:
            session.add(
                ExperimentRun(
                    case_name="case1",
                    architecture="baseline",
                    run_index=1,
                    model_name="test-model",
                    prompt_version="v1",
                    status="success",
                    schema_valid=True,
                    unsupported_skill_claims=0,
                    llm_calls=1,
                    estimated_tokens=128,
                    latency_ms=12.5,
                    error_codes="[]",
                    output_json="{}",
                    created_at=datetime.now(timezone.utc),
                )
            )
            session.commit()

        with session_factory() as session:
            rows = session.query(ExperimentRun).all()
    finally:
        engine.dispose()

    assert len(rows) == 1
    assert rows[0].case_name == "case1"