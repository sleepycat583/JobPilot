"""Run isolated end-to-end business flows against the configured real model.

The script uses a temporary SQLite database, upload directory, checkpoint file,
and local Chroma directory. It prints only flow names and aggregate facts; it
never prints model output, source documents, trace payloads, or credentials.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import get_settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import ExecutionEventRecord  # noqa: E402
from app.services.store import loads  # noqa: E402


POLL_TIMEOUT_SECONDS = 300.0
FORBIDDEN_OUTPUT_MARKERS = ("tool_call", "raw_state", "route_audit", "confidence", "api_key")


def _key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid4())}


def _wait_for_job(client: TestClient, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        body = response.json()
        if body["status"] in {"completed", "failed"}:
            assert body["status"] == "completed"
            return body
        time.sleep(0.2)
    raise AssertionError("background job timed out")


def _wait_for_thread(client: TestClient, thread_id: str, expected: set[str]) -> dict[str, Any]:
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        response = client.get(f"/api/threads/{thread_id}/state")
        assert response.status_code == 200
        body = response.json()
        if body["status"] in expected:
            return body
        time.sleep(0.2)
    raise AssertionError(f"thread did not reach one of {sorted(expected)}")


def _create_thread(client: TestClient) -> str:
    response = client.post("/api/threads", headers=_key())
    assert response.status_code == 201
    return str(response.json()["thread_id"])


def _assert_sse_sanitized(client: TestClient, thread_id: str) -> None:
    with client.app.state.session_factory() as session:
        records = session.query(ExecutionEventRecord).filter_by(stream_id=thread_id).all()
        payloads = [loads(record.payload_json, {}) for record in records]
    serialized = json.dumps(payloads, ensure_ascii=False).lower()
    assert all(marker not in serialized for marker in FORBIDDEN_OUTPUT_MARKERS)


def _create_resume(client: TestClient) -> str:
    source = (
        "姓名：测试候选人\n"
        "邮箱：candidate@example.test\n"
        "电话：13800138000\n"
        "目标岗位：后端开发工程师\n"
        "工作经历：负责 Java Spring Boot 交易服务，使用 Redis 和 Kafka 支撑异步任务。\n"
        "项目成果：通过压测和监控定位慢查询，接口 P95 延迟下降 35%。\n"
        "技能：Java、Spring Boot、MySQL、Redis、Kafka、Docker。\n"
    ).encode("utf-8")
    headers = _key()
    first = client.post(
        "/api/resumes",
        headers=headers,
        files={"file": ("synthetic-resume.txt", source, "text/plain")},
    )
    duplicate = client.post(
        "/api/resumes",
        headers=headers,
        files={"file": ("synthetic-resume.txt", source, "text/plain")},
    )
    assert first.status_code == duplicate.status_code == 202
    assert first.json() == duplicate.json()
    accepted = first.json()
    _wait_for_job(client, accepted["job_id"])
    resume = client.get(f"/api/resumes/{accepted['resource_id']}")
    assert resume.status_code == 200
    body = resume.json()
    assert body["status"] == "indexed"
    assert body["structured"]["privacy_filtered"] is True
    assert body["structured"]["chunk_count"] > 0
    return str(accepted["resource_id"])


def _create_jd(client: TestClient) -> str:
    text = (
        "职位：高级 Java 后端工程师。\n"
        "职责：负责交易平台核心服务、异步任务和稳定性治理，参与性能优化。\n"
        "要求：5 年以内 Java 开发经验，熟悉 Spring Boot、MySQL、Redis、Kafka；"
        "有高并发系统经验优先。"
    )
    headers = _key()
    first = client.post("/api/jds", headers=headers, json={"text": text})
    duplicate = client.post("/api/jds", headers=headers, json={"text": text})
    assert first.status_code == duplicate.status_code == 202
    assert first.json() == duplicate.json()
    accepted = first.json()
    _wait_for_job(client, accepted["job_id"])
    jd = client.get(f"/api/jds/{accepted['resource_id']}")
    assert jd.status_code == 200
    body = jd.json()
    assert body["status"] == "completed"
    assert body["parsed"]["evidence_preserved"] is True
    assert body["parsed"]["required_skills"]
    return str(accepted["resource_id"])


def _run_chat_flow(client: TestClient, thread_id: str, resume_id: str, jd_id: str) -> None:
    response = client.post(
        f"/api/threads/{thread_id}/messages",
        headers=_key(),
        json={
            "content": "你好，请给我一句准备技术面试的建议。",
            "resume_id": resume_id,
            "jd_id": jd_id,
        },
    )
    assert response.status_code == 202
    state = _wait_for_thread(client, thread_id, {"completed"})
    assert state["messages"][-1]["role"] == "assistant"
    assert state["messages"][-1]["content"].strip()
    _assert_sse_sanitized(client, thread_id)


def _run_match_flow(client: TestClient, thread_id: str, resume_id: str, jd_id: str) -> None:
    response = client.post(
        "/api/matches",
        headers=_key(),
        json={"thread_id": thread_id, "resume_id": resume_id, "jd_id": jd_id, "strict": False},
    )
    assert response.status_code == 202
    state = _wait_for_thread(client, thread_id, {"completed"})
    result = state["match_result"]
    assert 0 <= result["total_score"] <= 100
    assert set(result["dimension_scores"]) == {"必备技能", "核心职责", "加分技能", "硬性条件", "证据质量"}
    assert result["evidence_count"] > 0
    assert result["evidence"]
    _assert_sse_sanitized(client, thread_id)


def _run_interview_flow(client: TestClient, thread_id: str, resume_id: str, jd_id: str) -> None:
    started = client.post(
        "/api/interviews",
        headers=_key(),
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
    state = _wait_for_thread(client, thread_id, {"interrupted"})
    interrupt = state["pending_interrupt"]
    assert interrupt["type"] == "interview_answer"
    assert state["interview"]["current_question"].strip()

    feedback = client.post(
        f"/api/threads/{thread_id}/resume",
        headers=_key(),
        json={
            "interrupt_id": interrupt["id"],
            "action": "submit_answer",
            "payload": {"answer": "我先定位监控和日志，再用压测复现问题，最后通过灰度发布验证修复。"},
        },
    )
    assert feedback.status_code == 200
    feedback_state = feedback.json()
    assert feedback_state["interview"]["phase"] == "feedback"
    assert 0 <= feedback_state["interview"]["feedback"]["score"] <= 100

    ended = client.post(
        f"/api/threads/{thread_id}/resume",
        headers=_key(),
        json={
            "interrupt_id": feedback_state["pending_interrupt"]["id"],
            "action": "end",
            "payload": {},
        },
    )
    assert ended.status_code == 200
    report_state = ended.json()
    assert report_state["status"] == "completed"
    assert report_state["interview"]["phase"] == "report"
    report = report_state["interview"]["report"]
    assert 0 <= report["overall_score"] <= 100
    assert set(report["dimension_scores"]) == {"技术深度", "证据充分度", "岗位匹配度", "表达清晰度"}
    assert len(report["summary"]) <= 160
    assert len(report["actions"]) <= 4
    assert all(len(action) <= 180 for action in report["actions"])
    assert report_state["interview"]["records"]
    _assert_sse_sanitized(client, thread_id)


def main() -> int:
    settings = get_settings()
    if settings.llm_mode != "openai":
        raise RuntimeError("Business flow evaluation requires LLM_MODE=openai")
    with tempfile.TemporaryDirectory(prefix="career-flow-eval-") as temp_dir:
        root = Path(temp_dir)
        os.environ.update(
            {
                "DATABASE_URL": f"sqlite:///{(root / 'app.db').as_posix()}",
                "UPLOAD_DIR": str(root / "uploads"),
                "GRAPH_CHECKPOINT_PATH": str(root / "graph.db"),
                "CHROMA_BACKEND": "local",
                "CHROMA_PERSIST_DIRECTORY": str(root / "chroma"),
                "UPLOAD_STORAGE_BACKEND": "local",
                "AUTO_CREATE_SCHEMA": "true",
                "CHECKPOINT_BACKEND": "sqlite",
                "MOCK_TASK_DELAY_SECONDS": "0.01",
            }
        )
        get_settings.cache_clear()
        with TestClient(create_app()) as client:
            resume_id = _create_resume(client)
            print("PASS resume_parse_and_index")
            jd_id = _create_jd(client)
            print("PASS jd_parse")
            chat_thread = _create_thread(client)
            _run_chat_flow(client, chat_thread, resume_id, jd_id)
            print("PASS chat_sse_and_sanitization")
            match_thread = _create_thread(client)
            _run_match_flow(client, match_thread, resume_id, jd_id)
            print("PASS match_evidence_flow")
            interview_thread = _create_thread(client)
            _run_interview_flow(client, interview_thread, resume_id, jd_id)
            print("PASS interview_question_feedback_report")
    get_settings.cache_clear()
    print("summary: passed=5 failed=0 total=5")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
