from datetime import datetime, timedelta, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from app.core.config import Settings
from app.db import Base, build_engine, build_session_factory
from app.models import JobRecord
from app.services.store import claim_job, update_job


def _factory(tmp_path: Path):
    engine = build_engine(Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'jobs.db'}"))
    Base.metadata.create_all(engine)
    return engine, build_session_factory(engine)


def test_job_lease_allows_one_owner_at_a_time(tmp_path: Path) -> None:
    engine, session_factory = _factory(tmp_path)
    try:
        with session_factory() as session:
            session.add(JobRecord(id="job-1", kind="resume_parse", status="queued", progress=0))
            session.commit()

        with session_factory() as session:
            assert claim_job(session, "job-1", "worker-a", 900) is True
        with session_factory() as session:
            assert claim_job(session, "job-1", "worker-b", 900) is False

        with session_factory() as session:
            job = session.get(JobRecord, "job-1")
            assert job is not None
            update_job(session, job, status="completed", progress=100, lease_owner="worker-a")
        with session_factory() as session:
            assert claim_job(session, "job-1", "worker-b", 900) is False
    finally:
        engine.dispose()


def test_expired_job_lease_can_be_reclaimed(tmp_path: Path) -> None:
    engine, session_factory = _factory(tmp_path)
    try:
        with session_factory() as session:
            session.add(
                JobRecord(
                    id="job-2",
                    kind="jd_parse",
                    status="running",
                    progress=25,
                    lease_owner="dead-worker",
                    lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                )
            )
            session.commit()
        with session_factory() as session:
            assert claim_job(session, "job-2", "worker-b", 900) is True
            job = session.get(JobRecord, "job-2")
            assert job is not None and job.lease_owner == "worker-b"
    finally:
        engine.dispose()


def test_running_progress_renews_owned_lease(tmp_path: Path) -> None:
    engine, session_factory = _factory(tmp_path)
    try:
        with session_factory() as session:
            session.add(JobRecord(id="job-3", kind="jd_parse", status="queued", progress=0))
            session.commit()
            assert claim_job(session, "job-3", "worker-a", 30) is True
            job = session.get(JobRecord, "job-3")
            assert job is not None and job.lease_expires_at is not None
            before = job.lease_expires_at
            update_job(session, job, status="running", progress=50, lease_owner="worker-a", lease_seconds=900)
            session.refresh(job)
            assert job.lease_expires_at is not None and job.lease_expires_at > before
    finally:
        engine.dispose()


def test_concurrent_claim_has_exactly_one_winner(tmp_path: Path) -> None:
    engine, session_factory = _factory(tmp_path)
    try:
        with session_factory() as session:
            session.add(JobRecord(id="job-race", kind="resume_parse", status="queued", progress=0))
            session.commit()
        barrier = Barrier(2)

        def attempt(owner: str) -> bool:
            with session_factory() as session:
                barrier.wait()
                return claim_job(session, "job-race", owner, 900)

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(attempt, ["worker-a", "worker-b"]))
        assert sorted(outcomes) == [False, True]
    finally:
        engine.dispose()
