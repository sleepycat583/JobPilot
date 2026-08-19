"""Explicit retention cleanup for the single-user local runtime."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import ExecutionEventRecord, IdempotencyRecord, JobRecord


TERMINAL_JOB_STATUSES = {"completed", "failed"}


def preview_local_cleanup(session: Session, *, retention_days: int) -> dict[str, int]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    return {
        "terminal_jobs": len(
            list(session.scalars(select(JobRecord.id).where(JobRecord.status.in_(TERMINAL_JOB_STATUSES), JobRecord.updated_at < cutoff)))
        ),
        "idempotency_records": len(list(session.scalars(select(IdempotencyRecord.id).where(IdempotencyRecord.created_at < cutoff)))),
        "execution_events": len(list(session.scalars(select(ExecutionEventRecord.id).where(ExecutionEventRecord.created_at < cutoff)))),
    }


def apply_local_cleanup(session: Session, *, retention_days: int) -> dict[str, int]:
    """Delete disposable records only after the caller chose an explicit apply action."""

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    result = {
        "terminal_jobs": int(
            session.execute(delete(JobRecord).where(JobRecord.status.in_(TERMINAL_JOB_STATUSES), JobRecord.updated_at < cutoff)).rowcount or 0
        ),
        "idempotency_records": int(
            session.execute(delete(IdempotencyRecord).where(IdempotencyRecord.created_at < cutoff)).rowcount or 0
        ),
        "execution_events": int(
            session.execute(delete(ExecutionEventRecord).where(ExecutionEventRecord.created_at < cutoff)).rowcount or 0
        ),
    }
    session.commit()
    return result
