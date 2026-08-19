from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.config import Settings
from app.db import Base, build_engine, build_session_factory
from app.models import ExecutionEventRecord, IdempotencyRecord, JobRecord
from app.services.local_maintenance import apply_local_cleanup, preview_local_cleanup


def test_local_cleanup_only_removes_expired_disposable_records(tmp_path: Path) -> None:
    engine = build_engine(Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'app.db'}"))
    factory = build_session_factory(engine)
    old = datetime.now(timezone.utc) - timedelta(days=31)
    try:
        Base.metadata.create_all(engine)
        with factory() as session:
            session.add_all([
                JobRecord(id="old-job", kind="jd_parse", status="completed", progress=100, updated_at=old),
                JobRecord(id="active-job", kind="jd_parse", status="running", progress=25, updated_at=old),
                IdempotencyRecord(scope="old", key="old", request_hash="a", status_code=202, response_json="{}", created_at=old),
                ExecutionEventRecord(stream_type="thread", stream_id="thread-1", event_type="run_completed", created_at=old),
            ])
            session.commit()
            assert preview_local_cleanup(session, retention_days=30) == {
                "terminal_jobs": 1,
                "idempotency_records": 1,
                "execution_events": 1,
            }
            assert apply_local_cleanup(session, retention_days=30) == {
                "terminal_jobs": 1,
                "idempotency_records": 1,
                "execution_events": 1,
            }
            assert session.get(JobRecord, "old-job") is None
            assert session.get(JobRecord, "active-job") is not None
    finally:
        engine.dispose()
