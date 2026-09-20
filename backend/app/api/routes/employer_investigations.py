from uuid import uuid4

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import api_error
from app.api.helpers import cached_body, require_idempotency_key
from app.api.serializers import serialize_employer_investigation
from app.db import get_db
from app.models import EmployerInvestigationRecord, ThreadRecord
from app.schemas.contracts import EmployerInvestigationCreate, EmployerInvestigationRead, RunAccepted
from app.services.idempotency import hash_request, store_response
from app.services.store import load_thread_state, save_thread_state

router = APIRouter(prefix="/employer-investigations", tags=["employer investigations"])


@router.get("", response_model=list[EmployerInvestigationRead])
def list_investigations(thread_id: str | None = None, limit: int = 20, session: Session = Depends(get_db)):
    statement = select(EmployerInvestigationRecord).order_by(EmployerInvestigationRecord.created_at.desc()).limit(max(1, min(limit, 100)))
    if thread_id:
        statement = statement.where(EmployerInvestigationRecord.thread_id == thread_id)
    return [serialize_employer_investigation(item) for item in session.scalars(statement)]


@router.get("/{report_id}", response_model=EmployerInvestigationRead)
def get_investigation(report_id: str, session: Session = Depends(get_db)):
    record = session.get(EmployerInvestigationRecord, report_id)
    if record is None:
        raise api_error(404, "EMPLOYER_INVESTIGATION_NOT_FOUND", "雇主背调报告不存在。")
    return serialize_employer_investigation(record)


@router.post("", response_model=RunAccepted, status_code=202)
async def create_investigation(
    payload: EmployerInvestigationCreate,
    request: Request,
    response: Response,
    idempotency_key: str = Depends(require_idempotency_key),
    session: Session = Depends(get_db),
) -> RunAccepted:
    if not payload.company_name and not payload.unified_social_credit_code:
        raise api_error(422, "EMPLOYER_SUBJECT_REQUIRED", "请提供公司名称或统一社会信用代码。")
    thread = session.get(ThreadRecord, payload.thread_id)
    if thread is None:
        raise api_error(404, "THREAD_NOT_FOUND", "会话不存在。")
    state = load_thread_state(thread)
    if state.get("status") in {"running", "interrupted"}:
        raise api_error(409, "THREAD_BUSY", "当前会话正在执行其他任务。", retryable=True)
    request_hash = hash_request(payload.model_dump_json().encode())
    cached = cached_body(session, scope=f"thread:{payload.thread_id}:employer", key=idempotency_key, request_hash=request_hash)
    if cached:
        response.status_code = cached[0]
        return RunAccepted.model_validate(cached[1])
    run_id = str(uuid4())
    state.update({"status": "running", "pending_interrupt": None, "task": {"status": "running", "title": "正在执行雇主背调", "detail": "正在核验企业主体并查询企查查风险信息。", "steps": [{"label": "确认企业主体", "status": "active"}, {"label": "查询经营与风险信息", "status": "pending"}, {"label": "生成结构化报告", "status": "pending"}]}})
    save_thread_state(session, thread, state)
    body = RunAccepted(run_id=run_id, thread_id=payload.thread_id, status="running")
    store_response(session, scope=f"thread:{payload.thread_id}:employer", key=idempotency_key, request_hash=request_hash, status_code=202, body=body.model_dump(mode="json"))
    content = payload.unified_social_credit_code or payload.company_name or ""
    request.app.state.graph_runtime.spawn(request.app.state.graph_runtime.process_employer_due_diligence(payload.thread_id, run_id, content))
    return body
