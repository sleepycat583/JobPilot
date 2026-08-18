from uuid import uuid4

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import api_error
from app.api.helpers import cached_body, require_idempotency_key
from app.api.serializers import serialize_jd
from app.db import get_db
from app.models import JDRecord, JobRecord
from app.schemas.contracts import JDCreate, JDRead, JobAccepted
from app.services.idempotency import hash_request, store_response
from app.services.store import dumps


router = APIRouter(prefix="/jds", tags=["job descriptions"])


@router.post("", response_model=JobAccepted, status_code=202)
async def create_jd(
    payload: JDCreate,
    request: Request,
    response: Response,
    idempotency_key: str = Depends(require_idempotency_key),
    session: Session = Depends(get_db),
) -> JobAccepted:
    request_hash = hash_request(payload.model_dump_json().encode())
    cached = cached_body(session, scope="jd:create", key=idempotency_key, request_hash=request_hash)
    if cached:
        response.status_code = cached[0]
        return JobAccepted.model_validate(cached[1])
    jd_id, job_id = str(uuid4()), str(uuid4())
    session.add(JDRecord(id=jd_id, source_text=payload.text, status="processing"))
    session.add(JobRecord(id=job_id, kind="jd_parse", status="queued", progress=0, payload_json=dumps({"jd_id": jd_id})))
    session.commit()
    body = JobAccepted(job_id=job_id, resource_id=jd_id, status="queued", status_url=f"/api/jobs/{job_id}")
    store_response(session, scope="jd:create", key=idempotency_key, request_hash=request_hash, status_code=202, body=body.model_dump(mode="json"))
    request.app.state.runtime.spawn(request.app.state.runtime.process_jd(job_id))
    return body


@router.get("", response_model=list[JDRead])
def list_jds(session: Session = Depends(get_db)) -> list[JDRead]:
    records = session.scalars(select(JDRecord).order_by(JDRecord.created_at.desc())).all()
    return [serialize_jd(record) for record in records]


@router.get("/{jd_id}", response_model=JDRead)
def get_jd(jd_id: str, session: Session = Depends(get_db)) -> JDRead:
    record = session.get(JDRecord, jd_id)
    if record is None:
        raise api_error(404, "JD_NOT_FOUND", "职位描述不存在。")
    return serialize_jd(record)
