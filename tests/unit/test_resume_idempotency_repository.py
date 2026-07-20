"""Resume 幂等业务记录仓储测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db import Base, ResumeIdempotencyRecord, build_session_factory, create_sqlalchemy_engine
from app.repositories.resume_idempotency import (
    RESUME_LEASE_SECONDS,
    ResumeIdempotencyConflictError,
    ResumeIdempotencyRepository,
)


def _repository(tmp_path):
    engine = create_sqlalchemy_engine(f"sqlite:///{(tmp_path / 'business.sqlite3').as_posix()}")
    Base.metadata.create_all(engine)
    return engine, build_session_factory(engine)


def test_create_processing_persists_unique_explicit_key(tmp_path) -> None:
    engine, factory = _repository(tmp_path)
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    try:
        with factory() as session:
            repository = ResumeIdempotencyRepository(session)
            record = repository.create_processing(
                thread_id="thread-1", session_id="session-1", idempotency_key="key-1",
                command_fingerprint="a" * 64, now=now,
            )
            assert record.status == "processing"
            assert record.lease_expires_at.replace(tzinfo=timezone.utc) == now + timedelta(seconds=RESUME_LEASE_SECONDS)
            with pytest.raises(ResumeIdempotencyConflictError, match="RESUME_IDEMPOTENCY_CONFLICT"):
                repository.create_processing(
                    thread_id="thread-1", session_id="session-1", idempotency_key="key-1",
                    command_fingerprint="a" * 64, now=now,
                )
            assert session.query(ResumeIdempotencyRecord).count() == 1
    finally:
        engine.dispose()


def test_reclaim_expired_lease_records_visible_recovery_reason(tmp_path) -> None:
    engine, factory = _repository(tmp_path)
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    try:
        with factory() as session:
            repository = ResumeIdempotencyRepository(session)
            record = repository.create_processing(
                thread_id="thread-1", session_id="session-1", idempotency_key="key-1",
                command_fingerprint="a" * 64, now=now,
            )
            assert repository.reclaim_expired_lease(record, now=now + timedelta(seconds=29)) is False
            assert repository.reclaim_expired_lease(record, now=now + timedelta(seconds=31)) is True
            assert record.error_code == "RESUME_LEASE_EXPIRED"
            assert record.lease_expires_at.replace(tzinfo=timezone.utc) == now + timedelta(seconds=31 + RESUME_LEASE_SECONDS)
    finally:
        engine.dispose()