"""实验运行仓储测试。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.db import Base, ExperimentRun, build_session_factory, create_sqlalchemy_engine
from app.repositories.experiment import ExperimentRunRepository


@pytest.mark.core_agent_tests
def test_experiment_run_repository_persists_row_and_coerces_iso_datetime(tmp_path) -> None:
    """仓储应能把脚本行数据写入 ORM 表，并接受 ISO 时间字符串。"""

    database_path = tmp_path / "app.sqlite3"
    engine = create_sqlalchemy_engine(f"sqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(engine)
    session_factory = build_session_factory(engine)

    try:
        with session_factory() as session:
            entity = ExperimentRunRepository(session).create_run(
                {
                    "case_name": "case1",
                    "architecture": "baseline",
                    "run_index": 1,
                    "model_name": "fake-model",
                    "prompt_version": "v1",
                    "status": "success",
                    "schema_valid": 1,
                    "unsupported_skill_claims": 0,
                    "llm_calls": 1,
                    "estimated_tokens": 32,
                    "latency_ms": 15.2,
                    "error_codes": "[]",
                    "output_json": "{}",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            )

        with session_factory() as session:
            rows = session.query(ExperimentRun).all()
    finally:
        engine.dispose()

    assert entity.id > 0
    assert len(rows) == 1
    assert rows[0].architecture == "baseline"