"""实验采样五元组幂等约束测试。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.db import Base, ExperimentRun, build_session_factory, create_sqlalchemy_engine
from app.repositories.experiment import ExperimentRunAlreadyExistsError, ExperimentRunRepository


def _row(run_index: int) -> dict[str, object]:
    return {
        "case_name": "case1", "architecture": "baseline", "run_index": run_index,
        "model_name": "fake-model", "prompt_version": "v1", "status": "success",
        "schema_valid": 1, "unsupported_skill_claims": 0, "llm_calls": 1,
        "estimated_tokens": 32, "latency_ms": 15.2, "error_codes": "[]",
        "output_json": "{}", "created_at": datetime.now(timezone.utc).isoformat(),
    }


def test_duplicate_sampling_key_raises_without_second_row(tmp_path) -> None:
    engine = create_sqlalchemy_engine(f"sqlite:///{(tmp_path / 'app.sqlite3').as_posix()}")
    Base.metadata.create_all(engine)
    factory = build_session_factory(engine)
    try:
        with factory() as session:
            repository = ExperimentRunRepository(session)
            repository.create_run(_row(1))
            with pytest.raises(ExperimentRunAlreadyExistsError, match="EXPERIMENT_RUN_ALREADY_EXISTS"):
                repository.create_run(_row(1))
            assert session.query(ExperimentRun).count() == 1
    finally:
        engine.dispose()