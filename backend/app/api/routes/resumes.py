from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import api_error
from app.api.helpers import cached_body, require_idempotency_key
from app.api.serializers import serialize_resume
from app.db import get_db
from app.models import JobRecord, ResumeRecord
from app.schemas.contracts import JobAccepted, ResumeRead
from app.services.idempotency import hash_request, store_response
from app.services.store import dumps


router = APIRouter(prefix="/resumes", tags=["resumes"])
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt"}


@router.post("", response_model=JobAccepted, status_code=202)
async def upload_resume(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    idempotency_key: str = Depends(require_idempotency_key),
    session: Session = Depends(get_db),
) -> JobAccepted:
    content = await file.read()
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise api_error(415, "UNSUPPORTED_FILE_TYPE", "仅支持 PDF、DOCX 和 TXT 文件。")
    if not content:
        raise api_error(400, "EMPTY_FILE", "上传文件不能为空。")
    if len(content) > request.app.state.settings.max_upload_bytes:
        raise api_error(413, "FILE_TOO_LARGE", "上传文件超过大小限制。")

    request_hash = hash_request((file.filename or "").encode(), (file.content_type or "").encode(), content)
    cached = cached_body(session, scope="resume:create", key=idempotency_key, request_hash=request_hash)
    if cached:
        response.status_code = cached[0]
        return JobAccepted.model_validate(cached[1])

    resume_id = str(uuid4())
    job_id = str(uuid4())
    upload_key = f"{resume_id}{suffix}"
    try:
        request.app.state.blob_store.put_bytes(
            upload_key,
            content,
            content_type=file.content_type or "application/octet-stream",
        )
    except Exception as exc:
        raise api_error(503, "UPLOAD_STORAGE_UNAVAILABLE", "文件存储暂时不可用，请稍后重试。", retryable=True) from exc
    digest = hash_request(content)
    resume = ResumeRecord(
        id=resume_id,
        display_name=Path(file.filename or "简历").stem,
        file_name=file.filename or f"resume{suffix}",
        content_type=file.content_type or "application/octet-stream",
        size_bytes=len(content),
        file_sha256=digest,
        status="processing",
    )
    job = JobRecord(
        id=job_id,
        kind="resume_parse",
        status="queued",
        progress=0,
        payload_json=dumps({"resume_id": resume_id, "upload_key": upload_key}),
    )
    session.add_all([resume, job])
    session.commit()
    body = JobAccepted(job_id=job_id, resource_id=resume_id, status="queued", status_url=f"/api/jobs/{job_id}")
    store_response(session, scope="resume:create", key=idempotency_key, request_hash=request_hash, status_code=202, body=body.model_dump(mode="json"))
    request.app.state.runtime.spawn(request.app.state.runtime.process_resume(job_id))
    return body


@router.get("", response_model=list[ResumeRead])
def list_resumes(session: Session = Depends(get_db)) -> list[ResumeRead]:
    records = session.scalars(select(ResumeRecord).order_by(ResumeRecord.created_at.desc())).all()
    return [serialize_resume(record) for record in records]


@router.get("/{resume_id}", response_model=ResumeRead)
def get_resume(resume_id: str, session: Session = Depends(get_db)) -> ResumeRead:
    record = session.get(ResumeRecord, resume_id)
    if record is None:
        raise api_error(404, "RESUME_NOT_FOUND", "简历不存在。")
    return serialize_resume(record)
