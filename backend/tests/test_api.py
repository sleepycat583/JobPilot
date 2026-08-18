import json
import time
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.routes.events import _sse
from app.models import ExecutionEventRecord
from app.services.store import loads


def key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid4())}


def wait_for_job(client: TestClient, job_id: str) -> dict[str, Any]:
    for _ in range(100):
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        body = response.json()
        if body["status"] in {"completed", "failed"}:
            return body
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def wait_for_thread(client: TestClient, thread_id: str, status: str) -> dict[str, Any]:
    for _ in range(100):
        response = client.get(f"/api/threads/{thread_id}/state")
        assert response.status_code == 200
        body = response.json()
        if body["status"] == status:
            return body
        time.sleep(0.01)
    raise AssertionError(f"thread did not reach {status}")


def create_thread(client: TestClient) -> str:
    response = client.post("/api/threads", headers=key())
    assert response.status_code == 201
    return response.json()["thread_id"]


def create_resume(client: TestClient) -> str:
    response = client.post(
        "/api/resumes",
        headers=key(),
        files={"file": ("backend-resume.pdf", b"%PDF-1.4 mock resume", "application/pdf")},
    )
    assert response.status_code == 202
    body = response.json()
    assert wait_for_job(client, body["job_id"])["status"] == "completed"
    return body["resource_id"]


def create_jd(client: TestClient) -> str:
    response = client.post(
        "/api/jds",
        headers=key(),
        json={"text": "负责交易平台后端开发，需要 Java、Spring Boot、MySQL、Redis 与消息队列经验。"},
    )
    assert response.status_code == 202
    body = response.json()
    assert wait_for_job(client, body["job_id"])["status"] == "completed"
    return body["resource_id"]


def test_health_and_openapi(client: TestClient) -> None:
    assert client.get("/api/health/live").json() == {"status": "ok"}
    assert client.get("/api/health/ready").json() == {"status": "ready"}
    assert "/api/resumes" in client.get("/openapi.json").json()["paths"]


def test_resume_upload_is_async_and_idempotent(client: TestClient) -> None:
    headers = key()
    files = {"file": ("resume.pdf", b"%PDF-1.4 same", "application/pdf")}
    first = client.post("/api/resumes", headers=headers, files=files)
    second = client.post("/api/resumes", headers=headers, files=files)
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    completed = wait_for_job(client, first.json()["job_id"])
    resume = client.get(f"/api/resumes/{first.json()['resource_id']}").json()
    assert completed["progress"] == 100
    assert resume["status"] == "indexed"
    assert resume["structured"]["privacy_filtered"] is True


def test_idempotency_key_rejects_different_file(client: TestClient) -> None:
    headers = key()
    first = client.post("/api/resumes", headers=headers, files={"file": ("resume.pdf", b"first", "application/pdf")})
    second = client.post("/api/resumes", headers=headers, files={"file": ("resume.pdf", b"second", "application/pdf")})
    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_jd_parse_and_chat_events(client: TestClient) -> None:
    jd_id = create_jd(client)
    assert client.get(f"/api/jds/{jd_id}").json()["parsed"]["evidence_preserved"] is True
    thread_id = create_thread(client)
    sent = client.post(
        f"/api/threads/{thread_id}/messages",
        headers=key(),
        json={"content": "请告诉我下一步应该做什么", "jd_id": jd_id},
    )
    assert sent.status_code == 202
    state = wait_for_thread(client, thread_id, "completed")
    assert [message["role"] for message in state["messages"]][-2:] == ["user", "assistant"]
    assert "LangGraph Supervisor" in state["messages"][-1]["content"]
    assert state["task"]["title"] == "对话 Worker 已完成"


def test_low_match_interrupt_can_resume(client: TestClient) -> None:
    thread_id = create_thread(client)
    resume_id, jd_id = create_resume(client), create_jd(client)
    response = client.post(
        "/api/matches",
        headers=key(),
        json={"thread_id": thread_id, "resume_id": resume_id, "jd_id": jd_id, "strict": True},
    )
    assert response.status_code == 202
    state = wait_for_thread(client, thread_id, "interrupted")
    assert state["match_result"]["total_score"] == 54.0
    assert state["pending_interrupt"]["type"] == "low_match_score"
    resumed = client.post(
        f"/api/threads/{thread_id}/resume",
        headers=key(),
        json={"interrupt_id": state["pending_interrupt"]["id"], "action": "continue", "payload": {}},
    )
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "completed"
    assert resumed.json()["pending_interrupt"] is None


def test_interview_answer_feedback_and_next_question(client: TestClient) -> None:
    thread_id = create_thread(client)
    resume_id, jd_id = create_resume(client), create_jd(client)
    started = client.post(
        "/api/interviews",
        headers=key(),
        json={
            "thread_id": thread_id,
            "resume_id": resume_id,
            "jd_id": jd_id,
            "interview_type": "综合面试",
            "question_count": 3,
            "feedback_mode": "each",
        },
    )
    assert started.status_code == 202
    state = client.get(f"/api/threads/{thread_id}/state").json()
    question_interrupt = state["pending_interrupt"]
    feedback = client.post(
        f"/api/threads/{thread_id}/resume",
        headers=key(),
        json={
            "interrupt_id": question_interrupt["id"],
            "action": "submit_answer",
            "payload": {"answer": "我负责服务拆分，并通过压测验证吞吐提升。"},
        },
    ).json()
    assert feedback["interview"]["phase"] == "feedback"
    assert feedback["pending_interrupt"]["type"] == "interview_continue"
    assert feedback["task"]["title"] == "本题反馈已生成"
    next_state = client.post(
        f"/api/threads/{thread_id}/resume",
        headers=key(),
        json={"interrupt_id": feedback["pending_interrupt"]["id"], "action": "next", "payload": {}},
    ).json()
    assert next_state["interview"]["current_index"] == 1
    assert next_state["pending_interrupt"]["type"] == "interview_answer"


def test_sse_serialization_and_persisted_payloads_are_sanitized(client: TestClient) -> None:
    rendered = _sse(event_id=12, event="message_completed", data={"message": {"content": "结果"}})
    assert rendered == 'id: 12\nevent: message_completed\ndata: {"message":{"content":"结果"}}\n\n'
    thread_id = create_thread(client)
    client.post(f"/api/threads/{thread_id}/messages", headers=key(), json={"content": "测试事件净化"})
    wait_for_thread(client, thread_id, "completed")
    session_factory = client.app.state.session_factory
    with session_factory() as session:
        records = session.query(ExecutionEventRecord).filter_by(stream_id=thread_id).all()
    serialized = json.dumps([loads(record.payload_json, {}) for record in records], ensure_ascii=False).lower()
    for forbidden in ("prompt", "tool_call", "raw_state", "api_key", "route_audit", "confidence"):
        assert forbidden not in serialized


def test_chat_message_idempotency_does_not_duplicate_graph_run(client: TestClient) -> None:
    thread_id = create_thread(client)
    headers = key()
    payload = {"content": "请记录这是一条幂等消息"}
    first = client.post(f"/api/threads/{thread_id}/messages", headers=headers, json=payload)
    second = client.post(f"/api/threads/{thread_id}/messages", headers=headers, json=payload)
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    state = wait_for_thread(client, thread_id, "completed")
    assert sum(message["content"] == payload["content"] for message in state["messages"]) == 1
    with client.app.state.session_factory() as session:
        completed_events = session.query(ExecutionEventRecord).filter_by(
            stream_id=thread_id,
            event_type="message_completed",
        ).all()
    assert len(completed_events) == 1


def test_langgraph_checkpoint_is_persisted(client: TestClient) -> None:
    import sqlite3

    thread_id = create_thread(client)
    client.post(f"/api/threads/{thread_id}/messages", headers=key(), json={"content": "创建 checkpoint"})
    wait_for_thread(client, thread_id, "completed")
    checkpoint_path = client.app.state.settings.graph_checkpoint_path
    with sqlite3.connect(checkpoint_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()[0]
    assert count > 0
