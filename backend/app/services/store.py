import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExecutionEventRecord, JobRecord, ThreadRecord


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    return json.loads(value)


def initial_thread_state(thread_id: str) -> dict[str, Any]:
    return {
        "thread_id": thread_id,
        "status": "idle",
        "messages": [
            {
                "id": f"welcome-{thread_id}",
                "role": "assistant",
                "content": "你好，我可以帮你解析简历、分析职位描述、评估岗位匹配度，或者开始一场模拟面试。",
                "created_at": utc_iso(),
            }
        ],
        "task": {
            "status": "idle",
            "title": "等待任务",
            "detail": "发送消息后，这里会显示处理进度。",
            "steps": [],
        },
        "selected_resume_id": None,
        "selected_jd_id": None,
        "match_result": None,
        "interview": None,
        "pending_interrupt": None,
        "updated_at": utc_iso(),
    }


def load_thread_state(record: ThreadRecord) -> dict[str, Any]:
    return loads(record.state_json, initial_thread_state(record.id))


def save_thread_state(session: Session, record: ThreadRecord, state: dict[str, Any]) -> None:
    state["updated_at"] = utc_iso()
    record.status = state["status"]
    record.state_json = dumps(state)
    session.add(record)
    session.commit()


def append_event(
    session: Session,
    *,
    stream_type: str,
    stream_id: str,
    event_type: str,
    data: dict[str, Any],
) -> ExecutionEventRecord:
    event = ExecutionEventRecord(
        stream_type=stream_type,
        stream_id=stream_id,
        event_type=event_type,
        payload_json=dumps(data),
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


def events_after(
    session: Session,
    *,
    stream_type: str,
    stream_id: str,
    after_id: int,
) -> list[ExecutionEventRecord]:
    statement = (
        select(ExecutionEventRecord)
        .where(
            ExecutionEventRecord.stream_type == stream_type,
            ExecutionEventRecord.stream_id == stream_id,
            ExecutionEventRecord.id > after_id,
        )
        .order_by(ExecutionEventRecord.id)
    )
    return list(session.scalars(statement))


def update_job(
    session: Session,
    job: JobRecord,
    *,
    status: str,
    progress: int,
    result: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    job.status = status
    job.progress = progress
    job.result_json = dumps(result) if result is not None else job.result_json
    job.error_code = error_code
    job.error_message = error_message
    session.add(job)
    session.commit()
