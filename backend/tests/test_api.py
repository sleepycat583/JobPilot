import asyncio
import json
import time
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.routes.events import _sse
from app.models import ExecutionEventRecord, JobRecord, ThreadRecord
from app.services.llm_tasks import (
    InterviewFeedback,
    InterviewQuestion,
    InterviewReport,
    MatchEvidence,
    StructuredJD,
    StructuredMatch,
    StructuredResume,
)
from app.services.store import append_event, events_after, load_thread_state, loads, save_thread_state


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
    assert "/api/jobs/{job_id}/retry" in client.get("/openapi.json").json()["paths"]
    assert "/api/local-data/summary" in client.get("/openapi.json").json()["paths"]


def test_local_data_summary_backup_and_cleanup_controls(client: TestClient) -> None:
    create_resume(client)
    create_jd(client)
    summary = client.get("/api/local-data/summary")
    assert summary.status_code == 200
    body = summary.json()
    assert body["storage_mode"] == "local"
    assert body["counts"]["resumes"] == 1
    assert body["counts"]["job_descriptions"] == 1
    assert body["restore_requires_shutdown"] is True
    assert "path" not in json.dumps(body).lower()

    preview = client.get("/api/local-data/cleanup-preview?retention_days=30")
    assert preview.status_code == 200
    assert preview.json()["retention_days"] == 30

    invalid = client.post("/api/local-data/cleanup", headers=key(), json={"retention_days": 30, "confirmation": "wrong"})
    assert invalid.status_code == 422
    headers = key()
    cleaned = client.post(
        "/api/local-data/cleanup",
        headers=headers,
        json={"retention_days": 30, "confirmation": "DELETE_LOCAL_HISTORY"},
    )
    repeated = client.post(
        "/api/local-data/cleanup",
        headers=headers,
        json={"retention_days": 30, "confirmation": "DELETE_LOCAL_HISTORY"},
    )
    assert cleaned.status_code == repeated.status_code == 200
    assert cleaned.json() == repeated.json()

    backup = client.post("/api/local-data/backup")
    assert backup.status_code == 200
    assert backup.headers["content-type"].startswith("application/zip")
    assert backup.content.startswith(b"PK")


def test_failed_job_can_be_retried_idempotently(client: TestClient) -> None:
    response = client.post(
        "/api/resumes",
        headers=key(),
        files={"file": ("resume.txt", b"retryable resume", "text/plain")},
    )
    body = response.json()
    assert wait_for_job(client, body["job_id"])["status"] == "completed"
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, body["job_id"])
        assert job is not None
        job.status = "failed"
        job.error_code = "RESUME_MODEL_FAILED"
        job.error_message = "temporary"
        session.add(job)
        session.commit()
    retry_headers = key()
    retried = client.post(f"/api/jobs/{body['job_id']}/retry", headers=retry_headers)
    assert retried.status_code == 202
    repeated = client.post(f"/api/jobs/{body['job_id']}/retry", headers=retry_headers)
    assert repeated.status_code == 202
    assert repeated.json() == retried.json()
    assert wait_for_job(client, body["job_id"])["status"] == "completed"


def test_readiness_reports_uninitialized_app(client: TestClient) -> None:
    runtime = client.app.state.runtime
    graph_runtime = client.app.state.graph_runtime
    del client.app.state.runtime
    response = client.get("/api/health/ready")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "APP_NOT_READY"
    client.app.state.runtime = runtime
    client.app.state.graph_runtime = graph_runtime


def test_readiness_reports_unavailable_checkpoint(client: TestClient) -> None:
    checkpointer = client.app.state.graph_checkpointer

    class BrokenCheckpoint:
        def get_tuple(self, _config: dict[str, Any]) -> None:
            raise RuntimeError("checkpoint unavailable")

    client.app.state.graph_checkpointer = BrokenCheckpoint()
    response = client.get("/api/health/ready")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "CHECKPOINT_NOT_READY"
    client.app.state.graph_checkpointer = checkpointer


def test_readiness_reports_unavailable_shared_storage(client: TestClient) -> None:
    blob_store = client.app.state.blob_store

    class BrokenBlobStore:
        def probe(self) -> None:
            raise RuntimeError("storage unavailable")

    client.app.state.blob_store = BrokenBlobStore()
    response = client.get("/api/health/ready")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SHARED_STORAGE_NOT_READY"
    client.app.state.blob_store = blob_store


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


def test_match_and_interview_history_are_persisted(client: TestClient) -> None:
    thread_id = create_thread(client)
    resume_id, jd_id = create_resume(client), create_jd(client)
    matched = client.post(
        "/api/matches",
        headers=key(),
        json={"thread_id": thread_id, "resume_id": resume_id, "jd_id": jd_id, "strict": False},
    )
    assert matched.status_code == 202
    wait_for_thread(client, thread_id, "completed")
    reports = client.get(f"/api/matches?thread_id={thread_id}").json()
    assert len(reports) == 1
    assert reports[0]["result"]["total_score"] == 72.5
    assert reports[0]["resume_id"] == resume_id

    started = client.post(
        "/api/interviews",
        headers=key(),
        json={
            "thread_id": thread_id,
            "resume_id": resume_id,
            "jd_id": jd_id,
            "interview_type": "综合面试",
            "question_count": 3,
            "feedback_mode": "final",
        },
    )
    assert started.status_code == 202
    state = client.get(f"/api/threads/{thread_id}/state").json()
    finished = client.post(
        f"/api/threads/{thread_id}/resume",
        headers=key(),
        json={"interrupt_id": state["pending_interrupt"]["id"], "action": "end", "payload": {}},
    )
    assert finished.status_code == 200
    history = client.get(f"/api/interviews?thread_id={thread_id}").json()
    assert len(history) == 1
    assert history[0]["overall_score"] == 76
    assert history[0]["result"]["phase"] == "report"


def test_active_thread_can_be_cancelled_idempotently(client: TestClient) -> None:
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
    headers = key()
    first = client.post(f"/api/threads/{thread_id}/cancel", headers=headers)
    second = client.post(f"/api/threads/{thread_id}/cancel", headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["status"] == "cancelled"


def test_match_is_rejected_while_interview_is_interrupted(client: TestClient) -> None:
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

    match = client.post(
        "/api/matches",
        headers=key(),
        json={"thread_id": thread_id, "resume_id": resume_id, "jd_id": jd_id, "strict": False},
    )
    assert match.status_code == 409
    assert match.json()["error"]["code"] == "THREAD_BUSY"


def test_cancelled_thread_is_not_overwritten_by_late_graph_failure(client: TestClient) -> None:
    thread_id = create_thread(client)
    with client.app.state.session_factory() as session:
        thread = session.get(ThreadRecord, thread_id)
        assert thread is not None
        state = load_thread_state(thread)
        state["status"] = "cancelled"
        state["active_run_id"] = "late-run"
        save_thread_state(session, thread, state)

    asyncio.run(client.app.state.graph_runtime._finalize_failure(thread_id, "late-run"))

    state = client.get(f"/api/threads/{thread_id}/state").json()
    assert state["status"] == "cancelled"
    with client.app.state.session_factory() as session:
        events = session.query(ExecutionEventRecord).filter_by(stream_id=thread_id).all()
    assert not any(event.event_type == "run_failed" for event in events)


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


def test_chat_message_persists_ordered_stream_events_matching_final_message(client: TestClient) -> None:
    thread_id = create_thread(client)
    client.post(f"/api/threads/{thread_id}/messages", headers=key(), json={"content": "增量事件"})
    state = wait_for_thread(client, thread_id, "completed")
    with client.app.state.session_factory() as session:
        records = session.query(ExecutionEventRecord).filter_by(stream_id=thread_id).order_by(ExecutionEventRecord.id).all()
        payloads = [(record.event_type, loads(record.payload_json, {})) for record in records]
    stream = [payload for event, payload in payloads if event == "message_delta"]
    completed = next(payload for event, payload in payloads if event == "message_completed")
    assert [event for event, _ in payloads][-1] == "run_completed"
    assert [event for event, _ in payloads].count("message_started") == 1
    assert len(stream) >= 2
    assert "".join(item["delta"] for item in stream) == completed["message"]["content"] == state["messages"][-1]["content"]
    assert [item["index"] for item in stream] == list(range(len(stream)))


def test_cancelled_stream_cannot_write_final_assistant_message(client: TestClient) -> None:
    thread_id = create_thread(client)
    client.post(f"/api/threads/{thread_id}/messages", headers=key(), json={"content": "取消流式任务"})
    import time

    time.sleep(0.08)
    cancelled = client.post(f"/api/threads/{thread_id}/cancel", headers=key())
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    time.sleep(0.2)
    state = client.get(f"/api/threads/{thread_id}/state").json()
    assert state["status"] == "cancelled"
    assert not any(message["role"] == "assistant" and message["id"] != state["messages"][0]["id"] for message in state["messages"])


def test_sse_rejects_invalid_last_event_id(client: TestClient) -> None:
    thread_id = create_thread(client)
    response = client.get(f"/api/threads/{thread_id}/events", headers={"Last-Event-ID": "not-a-number"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "LAST_EVENT_ID_INVALID"


def test_sse_replays_only_events_after_last_event_id(client: TestClient) -> None:
    thread_id = create_thread(client)
    with client.app.state.session_factory() as session:
        first = append_event(session, stream_type="thread", stream_id=thread_id, event_type="node_started", data={"step": 1})
        second = append_event(session, stream_type="thread", stream_id=thread_id, event_type="run_completed", data={"step": 2})
        replay = events_after(session, stream_type="thread", stream_id=thread_id, after_id=first.id)
    assert [record.id for record in replay] == [second.id]

    class RequestStub:
        app = client.app
        calls = 0

        async def is_disconnected(self) -> bool:
            self.calls += 1
            return self.calls > 1

    async def collect() -> list[str]:
        from app.api.routes.events import thread_events

        response = await thread_events(thread_id, RequestStub(), last_event_id=str(first.id))
        return [chunk.decode() if isinstance(chunk, bytes) else chunk async for chunk in response.body_iterator]

    rendered = asyncio.run(collect())
    assert any("retry: 2000" in chunk for chunk in rendered)
    event_chunks = [chunk for chunk in rendered if "event: run_completed" in chunk]
    assert len(event_chunks) == 1
    assert '"step":2' in event_chunks[0]
    assert all('\"step\":1' not in chunk for chunk in event_chunks)


def test_chat_message_rejects_when_thread_is_already_running(client: TestClient) -> None:
    thread_id = create_thread(client)
    with client.app.state.session_factory() as session:
        thread = session.get(ThreadRecord, thread_id)
        assert thread is not None
        state = load_thread_state(thread)
        state["status"] = "running"
        save_thread_state(session, thread, state)
    response = client.post(f"/api/threads/{thread_id}/messages", headers=key(), json={"content": "并发请求"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "THREAD_BUSY"


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


class ContractTaskService:
    def parse_resume(self, _source_text: str, *, chunk_count: int) -> StructuredResume:
        return StructuredResume(
            profile="后端开发工程师",
            target_role="高级后端开发工程师",
            years=4,
            education="本科",
            skills=["Java", "Spring Boot"],
            chunk_count=chunk_count,
            privacy_filtered=True,
        )

    def parse_jd(self, _source_text: str) -> StructuredJD:
        return StructuredJD(
            job_title="高级后端开发工程师",
            responsibilities=["负责核心服务"],
            required_skills=["Java"],
            interview_focus=["服务稳定性"],
        )

    def match(self, _resume: dict[str, Any], _jd: dict[str, Any]) -> StructuredMatch:
        return StructuredMatch(
            total_score=76,
            dimension_scores={"必备技能": 32, "核心职责": 22, "加分技能": 6, "硬性条件": 8, "证据质量": 8},
            strengths=["Java 经验明确"],
            gaps=["稳定性证据不足"],
            evidence_count=1,
            evidence=[MatchEvidence(requirement="Java", resume_evidence="项目使用 Java", assessment="匹配", status="matched")],
        )

    def generate_question(
        self,
        _interview_type: str,
        _resume: dict[str, Any],
        _jd: dict[str, Any],
        _records: list[dict[str, Any]],
    ) -> InterviewQuestion:
        return InterviewQuestion(question="请介绍一次服务稳定性改造。", focus="稳定性")

    def evaluate_answer(
        self,
        _interview_type: str,
        _question: str,
        _answer: str,
        _resume: dict[str, Any],
        _jd: dict[str, Any],
    ) -> InterviewFeedback:
        return InterviewFeedback(score=81, title="证据清楚", detail="可以补充方案权衡。", tags=["结构清晰"])

    def report(
        self,
        _interview_type: str,
        _records: list[dict[str, Any]],
        _resume: dict[str, Any],
        _jd: dict[str, Any],
    ) -> InterviewReport:
        return InterviewReport(overall_score=80, summary="整体表现稳定", dimension_scores={"技术准确性": 82}, actions=["补充方案权衡"])


def test_real_task_contract_drives_parse_match_and_interview_state(client: TestClient) -> None:
    client.app.state.runtime.task_service = ContractTaskService()

    resume_response = client.post(
        "/api/resumes",
        headers=key(),
        files={"file": ("resume.txt", "Java 后端开发经历".encode(), "text/plain")},
    )
    resume_job = wait_for_job(client, resume_response.json()["job_id"])
    resume_id = resume_response.json()["resource_id"]
    resume = client.get(f"/api/resumes/{resume_id}").json()
    assert resume_job["status"] == "completed"
    assert resume["status"] == "parsed"
    assert resume["structured"]["profile"] == "后端开发工程师"

    jd_response = client.post(
        "/api/jds",
        headers=key(),
        json={"text": "招聘高级 Java 后端开发工程师，负责核心服务设计、开发与稳定性建设。"},
    )
    assert wait_for_job(client, jd_response.json()["job_id"])["status"] == "completed"
    jd_id = jd_response.json()["resource_id"]

    thread_id = create_thread(client)
    match_response = client.post(
        "/api/matches",
        headers=key(),
        json={"thread_id": thread_id, "resume_id": resume_id, "jd_id": jd_id, "strict": False},
    )
    assert match_response.status_code == 202
    match_state = wait_for_thread(client, thread_id, "completed")
    assert match_state["match_result"]["total_score"] == 76
    assert match_state["match_result"]["evidence"][0]["status"] == "matched"

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
    interview_state = client.get(f"/api/threads/{thread_id}/state").json()
    assert interview_state["interview"]["current_question"] == "请介绍一次服务稳定性改造。"

    feedback = client.post(
        f"/api/threads/{thread_id}/resume",
        headers=key(),
        json={
            "interrupt_id": interview_state["pending_interrupt"]["id"],
            "action": "submit_answer",
            "payload": {"answer": "我补充了监控并验证告警覆盖率。"},
        },
    ).json()
    assert feedback["interview"]["feedback"]["score"] == 81

    report = client.post(
        f"/api/threads/{thread_id}/resume",
        headers=key(),
        json={"interrupt_id": feedback["pending_interrupt"]["id"], "action": "end", "payload": {}},
    ).json()
    assert report["status"] == "completed"
    assert report["interview"]["report"]["overall_score"] == 80
