from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.errors import api_error
from app.api.helpers import cached_body, require_idempotency_key
from app.db import get_db
from app.models import JDRecord, ResumeRecord, ThreadRecord
from app.schemas.contracts import InterviewCreate, RunAccepted
from app.services.idempotency import hash_request, store_response
from app.services.store import load_thread_state


router = APIRouter(prefix="/interviews", tags=["interviews"])


@router.post("", response_model=RunAccepted, status_code=202)
def create_interview(
    payload: InterviewCreate,
    request: Request,
    response: Response,
    idempotency_key: str = Depends(require_idempotency_key),
    session: Session = Depends(get_db),
) -> RunAccepted:
    thread = session.get(ThreadRecord, payload.thread_id)
    if thread is None:
        raise api_error(404, "THREAD_NOT_FOUND", "会话不存在。")
    state = load_thread_state(thread)
    resume_id = payload.resume_id or state.get("selected_resume_id")
    jd_id = payload.jd_id or state.get("selected_jd_id")
    if not resume_id or session.get(ResumeRecord, resume_id) is None:
        raise api_error(422, "RESUME_REQUIRED", "请先选择已解析的简历。")
    if not jd_id or session.get(JDRecord, jd_id) is None:
        raise api_error(422, "JD_REQUIRED", "请先选择已解析的职位描述。")
    request_hash = hash_request(payload.model_dump_json().encode(), resume_id.encode(), jd_id.encode())
    cached = cached_body(session, scope=f"thread:{payload.thread_id}:interview", key=idempotency_key, request_hash=request_hash)
    if cached:
        response.status_code = cached[0]
        return RunAccepted.model_validate(cached[1])
    options = payload.model_dump()
    options.update({"resume_id": resume_id, "jd_id": jd_id})
    try:
        run_id = request.app.state.runtime.start_interview(payload.thread_id, options)
    except ValueError as exc:
        raise api_error(409, "INTERVIEW_INVALID", str(exc)) from exc
    body = RunAccepted(run_id=run_id, thread_id=payload.thread_id, status="interrupted")
    store_response(session, scope=f"thread:{payload.thread_id}:interview", key=idempotency_key, request_hash=request_hash, status_code=202, body=body.model_dump(mode="json"))
    return body
