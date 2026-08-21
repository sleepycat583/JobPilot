"""HTTP controls for safe personal-computer data management."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.errors import api_error
from app.api.helpers import cached_body, require_idempotency_key
from app.core.config import Settings
from app.db import get_db
from app.schemas.contracts import LocalCleanupPreview, LocalCleanupRequest, LocalDataSummary
from app.services.idempotency import hash_request, store_response
from app.services.local_data import backup_local_data, local_data_paths
from app.services.local_maintenance import apply_local_cleanup, preview_local_cleanup
from app.services.local_privacy import build_local_data_summary


router = APIRouter(prefix="/local-data", tags=["local-data"])


def _paths_or_409(settings: Settings):
    try:
        return local_data_paths(settings)
    except ValueError as exc:
        raise api_error(409, "LOCAL_DATA_MANAGEMENT_UNAVAILABLE", "当前存储模式不支持本地数据管理。") from exc


def _retention_days(settings: Settings, value: int | None) -> int:
    return value if value is not None else settings.local_history_retention_days


@router.get("/summary", response_model=LocalDataSummary)
def get_summary(request: Request, session: Session = Depends(get_db)) -> LocalDataSummary:
    settings: Settings = request.app.state.settings
    paths = _paths_or_409(settings)
    return build_local_data_summary(
        session,
        paths,
        retention_days=settings.local_history_retention_days,
        model_processing=settings.llm_mode,
    )


@router.get("/cleanup-preview", response_model=LocalCleanupPreview)
def get_cleanup_preview(
    request: Request,
    retention_days: int | None = Query(default=None, ge=1, le=3650),
    session: Session = Depends(get_db),
) -> LocalCleanupPreview:
    settings: Settings = request.app.state.settings
    _paths_or_409(settings)
    days = _retention_days(settings, retention_days)
    return LocalCleanupPreview(retention_days=days, **preview_local_cleanup(session, retention_days=days))


@router.post("/cleanup", response_model=LocalCleanupPreview)
def cleanup_history(
    payload: LocalCleanupRequest,
    request: Request,
    response: Response,
    idempotency_key: str = Depends(require_idempotency_key),
    session: Session = Depends(get_db),
) -> LocalCleanupPreview:
    settings: Settings = request.app.state.settings
    _paths_or_409(settings)
    request_hash = hash_request(payload.model_dump_json().encode())
    scope = "local-data:cleanup"
    cached = cached_body(session, scope=scope, key=idempotency_key, request_hash=request_hash)
    if cached:
        response.status_code = cached[0]
        return LocalCleanupPreview.model_validate(cached[1])
    result = LocalCleanupPreview(retention_days=payload.retention_days, **apply_local_cleanup(session, retention_days=payload.retention_days))
    store_response(session, scope=scope, key=idempotency_key, request_hash=request_hash, status_code=200, body=result.model_dump(mode="json"))
    return result


def _delete_archive(path: Path) -> None:
    path.unlink(missing_ok=True)


@router.post("/backup")
def download_backup(request: Request, background_tasks: BackgroundTasks) -> FileResponse:
    settings: Settings = request.app.state.settings
    paths = _paths_or_409(settings)
    descriptor, temporary_name = tempfile.mkstemp(prefix="career-agent-backup-", suffix=".zip")
    os.close(descriptor)
    Path(temporary_name).unlink(missing_ok=True)
    try:
        backup_local_data(paths, Path(temporary_name))
    except Exception as exc:
        Path(temporary_name).unlink(missing_ok=True)
        raise api_error(503, "LOCAL_BACKUP_FAILED", "本地备份创建失败，请稍后重试。", retryable=True) from exc
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    background_tasks.add_task(_delete_archive, Path(temporary_name))
    return FileResponse(
        temporary_name,
        media_type="application/zip",
        filename=f"career-agent-backup-{timestamp}.zip",
        background=background_tasks,
    )
