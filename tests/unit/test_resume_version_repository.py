"""简历版本与上传幂等仓储测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db import Base, build_session_factory, create_sqlalchemy_engine
from app.repositories.resume_versions import (
    UPLOAD_IDEMPOTENCY_RETENTION,
    ResumeUploadIdempotencyConflictError,
    ResumeVersionRepository,
)


def _repository(tmp_path):
    engine = create_sqlalchemy_engine(f"sqlite:///{(tmp_path / 'business.sqlite3').as_posix()}")
    Base.metadata.create_all(engine)
    return engine, build_session_factory(engine)


def _create(repository: ResumeVersionRepository, *, resume_id: str, key: str, fingerprint: str, now: datetime):
    """创建测试简历版本，集中固定与业务无关的文件元数据。"""

    return repository.create_version(
        resume_id=resume_id,
        file_name="resume.txt",
        file_size=42,
        storage_path=f"{resume_id}.txt",
        idempotency_key=key,
        request_fingerprint=fingerprint,
        now=now,
    )


def test_create_version_allocates_non_reusable_display_versions_and_records_upload_idempotency(tmp_path) -> None:
    engine, factory = _repository(tmp_path)
    now = datetime(2026, 7, 24, tzinfo=timezone.utc)
    try:
        with factory() as session:
            repository = ResumeVersionRepository(session)
            first = _create(repository, resume_id="00000000-0000-4000-8000-000000000001", key="00000000-0000-4000-8000-000000000101", fingerprint="a" * 64, now=now)
            second = _create(repository, resume_id="00000000-0000-4000-8000-000000000002", key="00000000-0000-4000-8000-000000000102", fingerprint="b" * 64, now=now)

            assert (first.display_version, second.display_version) == (1, 2)
            assert first.index_status == "pending"
            record = repository.get_upload_idempotency(idempotency_key="00000000-0000-4000-8000-000000000101")
            assert record is not None
            assert record.resume_id == first.resume_id
            assert record.expires_at.replace(tzinfo=timezone.utc) == now + UPLOAD_IDEMPOTENCY_RETENTION
            assert [item.display_version for item in repository.list_versions()] == [2, 1]
    finally:
        engine.dispose()


def test_replayed_upload_key_returns_original_version_without_allocating_another_display_version(tmp_path) -> None:
    engine, factory = _repository(tmp_path)
    now = datetime(2026, 7, 24, tzinfo=timezone.utc)
    try:
        with factory() as session:
            repository = ResumeVersionRepository(session)
            first = _create(repository, resume_id="00000000-0000-4000-8000-000000000001", key="00000000-0000-4000-8000-000000000101", fingerprint="a" * 64, now=now)
            replayed = _create(repository, resume_id="00000000-0000-4000-8000-000000000099", key="00000000-0000-4000-8000-000000000101", fingerprint="a" * 64, now=now + timedelta(seconds=1))

            assert replayed.resume_id == first.resume_id
            assert replayed.display_version == 1
            assert [item.display_version for item in repository.list_versions()] == [1]
    finally:
        engine.dispose()


def test_reused_upload_key_with_different_request_fingerprint_is_rejected(tmp_path) -> None:
    engine, factory = _repository(tmp_path)
    now = datetime(2026, 7, 24, tzinfo=timezone.utc)
    try:
        with factory() as session:
            repository = ResumeVersionRepository(session)
            _create(repository, resume_id="00000000-0000-4000-8000-000000000001", key="00000000-0000-4000-8000-000000000101", fingerprint="a" * 64, now=now)

            with pytest.raises(ResumeUploadIdempotencyConflictError, match="RESUME_UPLOAD_IDEMPOTENCY_KEY_REUSED"):
                _create(repository, resume_id="00000000-0000-4000-8000-000000000002", key="00000000-0000-4000-8000-000000000101", fingerprint="b" * 64, now=now)
    finally:
        engine.dispose()


def test_expired_upload_key_can_create_a_new_version_without_reusing_display_number(tmp_path) -> None:
    engine, factory = _repository(tmp_path)
    now = datetime(2026, 7, 24, tzinfo=timezone.utc)
    try:
        with factory() as session:
            repository = ResumeVersionRepository(session)
            _create(
                repository,
                resume_id="00000000-0000-4000-8000-000000000001",
                key="00000000-0000-4000-8000-000000000101",
                fingerprint="a" * 64,
                now=now,
            )
            next_version = _create(
                repository,
                resume_id="00000000-0000-4000-8000-000000000002",
                key="00000000-0000-4000-8000-000000000101",
                fingerprint="b" * 64,
                now=now + UPLOAD_IDEMPOTENCY_RETENTION,
            )

            assert next_version.display_version == 2
            assert next_version.resume_id == "00000000-0000-4000-8000-000000000002"
    finally:
        engine.dispose()