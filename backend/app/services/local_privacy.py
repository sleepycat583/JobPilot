"""Read-only local data inventory helpers for the personal workspace."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import InterviewRecord, JDRecord, MatchReportRecord, ResumeRecord, ThreadRecord
from app.schemas.contracts import LocalDataCounts, LocalDataStorage, LocalDataSummary
from app.services.local_data import LocalDataPaths


def _path_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    if not path.is_dir():
        return 0
    return sum(file.stat().st_size for file in path.rglob("*") if file.is_file())


def build_local_data_summary(
    session: Session,
    paths: LocalDataPaths,
    *,
    retention_days: int,
    model_processing: str,
) -> LocalDataSummary:
    return LocalDataSummary(
        storage_mode="local",
        model_processing="external_model" if model_processing == "openai" else "local_stub",
        retention_days=retention_days,
        counts=LocalDataCounts(
            resumes=int(session.scalar(select(func.count()).select_from(ResumeRecord)) or 0),
            job_descriptions=int(session.scalar(select(func.count()).select_from(JDRecord)) or 0),
            conversations=int(session.scalar(select(func.count()).select_from(ThreadRecord)) or 0),
            match_reports=int(session.scalar(select(func.count()).select_from(MatchReportRecord)) or 0),
            interview_reports=int(session.scalar(select(func.count()).select_from(InterviewRecord)) or 0),
            uploaded_files=sum(1 for file in paths.uploads.rglob("*") if file.is_file()) if paths.uploads.is_dir() else 0,
        ),
        storage=LocalDataStorage(
            business_database_bytes=_path_size(paths.database),
            checkpoint_database_bytes=_path_size(paths.checkpoint),
            uploads_bytes=_path_size(paths.uploads),
            vector_index_bytes=_path_size(paths.chroma),
        ),
    )
