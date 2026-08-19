from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.errors import api_error
from app.api.helpers import cached_body, require_idempotency_key
from app.api.serializers import serialize_job
from app.db import get_db
from app.models import JobRecord
from app.schemas.contracts import JobRead, JobRetryAccepted
from app.services.idempotency import hash_request, store_response
from app.services.store import loads


router = APIRouter(prefix="/jobs", tags=["jobs"])
RETRYABLE_JOB_ERRORS = {"RESUME_MODEL_FAILED", "RESUME_INDEX_FAILED", "JD_MODEL_FAILED"}


@router.get("/{job_id}", response_model=JobRead)
def get_job(job_id: str, session: Session = Depends(get_db)) -> JobRead:
    record = session.get(JobRecord, job_id)
    if record is None:
        raise api_error(404, "JOB_NOT_FOUND", "任务不存在。")
    return serialize_job(record)


@router.post("/{job_id}/retry", response_model=JobRetryAccepted, status_code=202)
async def retry_job(
    job_id: str,
    request: Request,
    response: Response,
    idempotency_key: str = Depends(require_idempotency_key),
    session: Session = Depends(get_db),
) -> JobRetryAccepted:
    record = session.get(JobRecord, job_id)
    if record is None:
        raise api_error(404, "JOB_NOT_FOUND", "任务不存在。")
    request_hash = hash_request(job_id.encode())
    scope = f"job:{job_id}:retry"
    cached = cached_body(session, scope=scope, key=idempotency_key, request_hash=request_hash)
    if cached:
        response.status_code = cached[0]
        return JobRetryAccepted.model_validate(cached[1])
    if record.status != "failed":
        raise api_error(409, "JOB_NOT_RETRYABLE", "只有失败任务可以重试。", retryable=False)
    if record.error_code not in RETRYABLE_JOB_ERRORS:
        raise api_error(409, "JOB_NOT_RETRYABLE", "该失败原因不支持重试。", retryable=False)
    if record.kind not in {"resume_parse", "jd_parse"}:
        raise api_error(409, "JOB_KIND_NOT_RETRYABLE", "该任务类型不支持重试。")
    payload = loads(record.payload_json, {})
    resource_id = payload.get("resume_id") or payload.get("jd_id")
    record.status = "queued"
    record.progress = 0
    record.result_json = None
    record.error_code = None
    record.error_message = None
    record.lease_owner = None
    record.lease_expires_at = None
    session.add(record)
    session.commit()
    body = JobRetryAccepted(job_id=job_id, resource_id=resource_id, status="queued", status_url=f"/api/jobs/{job_id}")
    store_response(session, scope=scope, key=idempotency_key, request_hash=request_hash, status_code=202, body=body.model_dump(mode="json"))
    runtime = request.app.state.runtime
    if record.kind == "resume_parse":
        runtime.spawn(runtime.process_resume(job_id))
    elif record.kind == "jd_parse":
        runtime.spawn(runtime.process_jd(job_id))
    return body
