"""Review 审计仓储测试。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.db import Base, ReviewAudit, build_session_factory, create_sqlalchemy_engine
from app.repositories.review_audit import ReviewAuditRepository


@pytest.mark.core_agent_tests
def test_review_audit_repository_creates_and_marks_completed(tmp_path) -> None:
    """审计仓储应能记录提交态并更新最终结果。"""

    database_path = tmp_path / "app.sqlite3"
    engine = create_sqlalchemy_engine(f"sqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(engine)
    session_factory = build_session_factory(engine)

    try:
        with session_factory() as session:
            repository = ReviewAuditRepository(session)
            audit = repository.create_submitted_audit(
                session_id="00000000-0000-4000-8000-000000000001",
                thread_id="thread-1",
                review_target="jd_parsed",
                action="approve",
                feedback=None,
                command_json='{"action":"approve"}',
                checkpoint_status_before="in_review",
            )
            completed = repository.mark_completed(
                audit.id,
                result="succeeded",
                completed_at=datetime.now(timezone.utc),
            )

        with session_factory() as session:
            rows = session.query(ReviewAudit).all()
    finally:
        engine.dispose()

    assert audit.id > 0
    assert completed.result == "succeeded"
    assert completed.completed_at is not None
    assert len(rows) == 1
    assert rows[0].action == "approve"