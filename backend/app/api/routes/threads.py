from uuid import uuid4

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import api_error
from app.api.helpers import cached_body, require_idempotency_key
from app.db import get_db
from app.models import JDRecord, ResumeRecord, ThreadRecord
from app.schemas.contracts import MessageCreate, ResumeCommand, RunAccepted, ThreadCreated, ThreadState
from app.services.idempotency import hash_request, store_response
from app.services.store import dumps, initial_thread_state, load_thread_state, save_thread_state, utc_iso


router = APIRouter(prefix="/threads", tags=["threads"])


def _thread_or_404(session: Session, thread_id: str) -> ThreadRecord:
    thread = session.get(ThreadRecord, thread_id)
    if thread is None:
        raise api_error(404, "THREAD_NOT_FOUND", "会话不存在。")
    return thread


def _locked_thread_or_404(session: Session, thread_id: str) -> ThreadRecord:
    thread = session.scalar(select(ThreadRecord).where(ThreadRecord.id == thread_id).with_for_update())
    if thread is None:
        raise api_error(404, "THREAD_NOT_FOUND", "会话不存在。")
    return thread


@router.post("", response_model=ThreadCreated, status_code=201)
def create_thread(
    response: Response,
    idempotency_key: str = Depends(require_idempotency_key),
    session: Session = Depends(get_db),
) -> ThreadCreated:
    request_hash = hash_request(b"create-thread-v1")
    cached = cached_body(session, scope="thread:create", key=idempotency_key, request_hash=request_hash)
    if cached:
        response.status_code = cached[0]
        return ThreadCreated.model_validate(cached[1])
    thread_id = str(uuid4())
    session.add(ThreadRecord(id=thread_id, status="idle", state_json=dumps(initial_thread_state(thread_id))))
    session.commit()
    body = ThreadCreated(
        thread_id=thread_id,
        state_url=f"/api/threads/{thread_id}/state",
        events_url=f"/api/threads/{thread_id}/events",
    )
    store_response(session, scope="thread:create", key=idempotency_key, request_hash=request_hash, status_code=201, body=body.model_dump(mode="json"))
    return body


@router.get("/{thread_id}/state", response_model=ThreadState)
def get_thread_state(thread_id: str, session: Session = Depends(get_db)) -> ThreadState:
    return ThreadState.model_validate(load_thread_state(_thread_or_404(session, thread_id)))


@router.post("/{thread_id}/messages", response_model=RunAccepted, status_code=202)
async def create_message(
    thread_id: str,
    payload: MessageCreate,
    request: Request,
    response: Response,
    idempotency_key: str = Depends(require_idempotency_key),
    session: Session = Depends(get_db),
) -> RunAccepted:
    thread = _locked_thread_or_404(session, thread_id)
    request_hash = hash_request(payload.model_dump_json().encode())
    cached = cached_body(session, scope=f"thread:{thread_id}:message", key=idempotency_key, request_hash=request_hash)
    if cached:
        response.status_code = cached[0]
        return RunAccepted.model_validate(cached[1])
    if payload.resume_id and session.get(ResumeRecord, payload.resume_id) is None:
        raise api_error(404, "RESUME_NOT_FOUND", "简历不存在。")
    if payload.jd_id and session.get(JDRecord, payload.jd_id) is None:
        raise api_error(404, "JD_NOT_FOUND", "职位描述不存在。")
    state = load_thread_state(thread)
    if state.get("status") == "running":
        raise api_error(409, "THREAD_BUSY", "当前会话正在处理上一条消息。", retryable=True)
    run_id = str(uuid4())
    state["messages"].append({"id": str(uuid4()), "role": "user", "content": payload.content, "created_at": utc_iso()})
    state["status"] = "running"
    state["active_run_id"] = run_id
    state["selected_resume_id"] = payload.resume_id or state.get("selected_resume_id")
    state["selected_jd_id"] = payload.jd_id or state.get("selected_jd_id")
    state["task"] = {
        "status": "running",
        "title": "正在接收你的需求",
        "detail": "请求已进入服务端执行队列。",
        "steps": [
            {"label": "接收用户消息", "status": "active"},
            {"label": "准备对话上下文", "status": "pending"},
            {"label": "组织可见结果", "status": "pending"},
        ],
    }
    save_thread_state(session, thread, state)
    body = RunAccepted(run_id=run_id, thread_id=thread_id, status="running")
    store_response(session, scope=f"thread:{thread_id}:message", key=idempotency_key, request_hash=request_hash, status_code=202, body=body.model_dump(mode="json"))
    request.app.state.graph_runtime.spawn(request.app.state.graph_runtime.process_message(thread_id, run_id, payload.content))
    return body


@router.post("/{thread_id}/resume", response_model=ThreadState)
def resume_thread(
    thread_id: str,
    payload: ResumeCommand,
    request: Request,
    response: Response,
    idempotency_key: str = Depends(require_idempotency_key),
    session: Session = Depends(get_db),
) -> ThreadState:
    _thread_or_404(session, thread_id)
    request_hash = hash_request(payload.model_dump_json().encode())
    cached = cached_body(session, scope=f"thread:{thread_id}:resume", key=idempotency_key, request_hash=request_hash)
    if cached:
        response.status_code = cached[0]
        return ThreadState.model_validate(cached[1])
    try:
        state = request.app.state.runtime.resume_interrupt(thread_id, payload.interrupt_id, payload.action, payload.payload)
    except ValueError as exc:
        raise api_error(409, "INTERRUPT_INVALID", str(exc)) from exc
    body = ThreadState.model_validate(state)
    store_response(session, scope=f"thread:{thread_id}:resume", key=idempotency_key, request_hash=request_hash, status_code=200, body=body.model_dump(mode="json"))
    return body
