from uuid import uuid4

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.errors import api_error
from app.api.helpers import cached_body, require_idempotency_key
from app.db import get_db
from app.models import JDRecord, ResumeRecord, ThreadRecord
from app.schemas.contracts import MatchCreate, RunAccepted
from app.services.idempotency import hash_request, store_response
from app.services.store import load_thread_state, save_thread_state


router = APIRouter(prefix="/matches", tags=["matches"])


@router.post("", response_model=RunAccepted, status_code=202)
async def create_match(
    payload: MatchCreate,
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
    cached = cached_body(session, scope=f"thread:{payload.thread_id}:match", key=idempotency_key, request_hash=request_hash)
    if cached:
        response.status_code = cached[0]
        return RunAccepted.model_validate(cached[1])
    if state.get("status") == "running":
        raise api_error(409, "THREAD_BUSY", "当前会话正在执行其他任务。", retryable=True)
    run_id = str(uuid4())
    state.update({
        "status": "running",
        "selected_resume_id": resume_id,
        "selected_jd_id": jd_id,
        "pending_interrupt": None,
        "task": {
            "status": "running",
            "title": "正在生成匹配报告",
            "detail": "将逐项核对 JD 与简历证据。",
            "steps": [
                {"label": "校验资料版本", "status": "done"},
                {"label": "检索简历证据", "status": "active"},
                {"label": "计算维度得分", "status": "pending"},
            ],
        },
    })
    save_thread_state(session, thread, state)
    body = RunAccepted(run_id=run_id, thread_id=payload.thread_id, status="running")
    store_response(session, scope=f"thread:{payload.thread_id}:match", key=idempotency_key, request_hash=request_hash, status_code=202, body=body.model_dump(mode="json"))
    request.app.state.runtime.spawn(request.app.state.runtime.process_match(payload.thread_id, run_id, payload.strict))
    return body
