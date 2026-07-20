"""FastAPI HTTP 与完整 Graph 集成验收。

测试通过 TestClient 调用真实编译 Graph；Chat Model 和简历存储均为 fake，
不访问外部 LLM、Embedding 或 Chroma 服务。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import asyncio
import threading
import json
import time
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import create_engine, text

from app.api import AppDependencies, create_app
from app.db import Base, build_session_factory, create_sqlalchemy_engine
from app.constants import MAX_INPUT_LENGTH
from app.graph.builder import build_graph

JD_TEXT = "后端工程师岗位，要求熟悉 Java、Spring Boot，并具备三年以上接口设计经验。"
JD_PARSED_JSON = (
    '{"job_title":"Java后端工程师","seniority":"mid","company_name":null,'
    '"responsibilities":["API design"],'
    '"skills":[{"name":"Java","category":"language","priority":"must","evidence":"熟悉 Java"}],'
    '"experience_requirements":["3年以上后端开发经验"],"education_requirements":[],'
    '"interview_focus":[],"company_context":[],"ambiguities":[],"source_language":"zh-CN"}'
)


@dataclass
class FakeChatModel:
    """按调用顺序返回固定 JSON 的 Chat Model。"""

    responses: list[str]
    invoke_calls: int = 0
    response_index: int = 0
    prompts: list[str] = field(default_factory=list)

    def invoke(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if any(marker in prompt for marker in ("InterviewPlanOutput", "QuestionProposal", "AnswerEvaluation", "InterviewReportNarrative")):
            response = self._interview_response(prompt)
        else:
            response = self.responses[self.response_index] if self.response_index < len(self.responses) else self.responses[-1]
            self.response_index += 1
        self.invoke_calls += 1
        return response

    def bind(self, **_: Any) -> "FakeChatModel":
        return self

    def _interview_response(self, prompt: str) -> str:
        if "InterviewPlanOutput" in prompt:
            return '{"plan":[{"topic_id":"project","topic":"项目经历","objective":"考察项目贡献","priority":"core","basis":"user_goal"},{"topic_id":"foundation","topic":"技术基础","objective":"考察基础","priority":"core","basis":"user_goal"}]}'
        if "QuestionProposal" in prompt:
            if "'question_id': 'q-1'" in prompt:
                return '{"topic":"技术基础","question":"请说明一次性能排查。"}'
            return '{"topic":"项目经历","question":"请介绍一个你负责的项目。"}'
        if "AnswerEvaluation" in prompt:
            return '{"scores":{"technical_accuracy":70,"structure":70,"job_relevance":70,"evidence":70},"feedback":"ok","strengths":[],"issues":[],"answer_relevance":"on_topic","fatal_error":false,"fatal_error_reason":null}'
        if "InterviewReportNarrative" in prompt:
            return '{"performance_summary":"样本不足。","recurring_strengths":[],"recurring_weaknesses":[],"review_actions":[],"question_references":[]}'
        return self.responses[-1]


def test_fake_chat_model_interview_response_fallback_returns_last_response() -> None:
    """兜底分支需要访问 self.responses，_interview_response 必须是实例方法而非 staticmethod。"""
    model = FakeChatModel(responses=["first", "last"])

    result = model._interview_response("prompt without any interview marker")

    assert result == "last"


@dataclass
class FakeResumeStore:
    """按需求和简历版本返回固定证据。"""

    mapping: dict[tuple[str, str], list[dict[str, Any]]]
    missing_versions: set[str] = field(default_factory=set)

    def query(self, query_text: str, resume_version: str) -> list[dict[str, Any]]:
        if resume_version in self.missing_versions:
            from app.rag.chroma_store import ResumeVersionNotFoundError

            raise ResumeVersionNotFoundError(resume_version)
        return self.mapping.get((query_text, resume_version), [])


def _route(route: str, task_queue: list[str] | None = None) -> str:
    return json.dumps({"route": route, "confidence": 0.9, "reason": "test", "task_queue": task_queue or []})


def _match_analysis(responsibility_relevance: float) -> str:
    return (
        '{"must_items":[{"requirement":"Java","status":"transferable","rationale":"ok",'
        '"evidence":[{"chunk_id":"java-1","quote":"Java"}],"recent":true,"quantified":true}],'
        f'"responsibility_items":[{{"requirement":"API design","status":"transferable","rationale":"ok",'
        f'"evidence":[{{"chunk_id":"api-1","quote":"API design {responsibility_relevance}"}}],'
        '"recent":true,"quantified":true}],"preferred_items":[],'
        '"constraint_items":[{"requirement":"3年以上后端开发经验","status":"satisfied","rationale":"ok",'
        '"evidence":[{"chunk_id":"exp-1","quote":"3 years"}]}],'
        '"strengths":["strong"],"gaps":[],"recommendations":[]}'
    )


def _client_for(
    *,
    responsibility_relevance: float = 0.4286,
    missing_versions: set[str] | None = None,
    jd_response: str = JD_PARSED_JSON,
    task_queue: list[str] | None = None,
    follow_up_responses: list[str] | None = None,
) -> TestClient:
    responses = [_route("jd_parse", task_queue or ["jd_parse", "resume_match"]), jd_response]
    if task_queue != ["jd_parse"]:
        responses.append(_match_analysis(responsibility_relevance))
    model = FakeChatModel(
        responses + (follow_up_responses or [])
    )
    store = FakeResumeStore(
        {
            ("Java", "2026-07-v1"): [{"chunk_id": "java-1", "quote": "Java", "relevance": 1.0}],
            ("API design", "2026-07-v1"): [
                {
                    "chunk_id": "api-1",
                    "quote": f"API design {responsibility_relevance}",
                    "relevance": responsibility_relevance,
                }
            ],
            ("3年以上后端开发经验", "2026-07-v1"): [
                {"chunk_id": "exp-1", "quote": "3 years", "relevance": 1.0}
            ],
            ("Java", "2026-07-v2"): [{"chunk_id": "java-1", "quote": "Java", "relevance": 1.0}],
            ("API design", "2026-07-v2"): [{"chunk_id": "api-1", "quote": "API design 1.0", "relevance": 1.0}],
            ("3年以上后端开发经验", "2026-07-v2"): [
                {"chunk_id": "exp-1", "quote": "3 years", "relevance": 1.0}
            ],
        },
        missing_versions=missing_versions or set(),
    )
    graph = build_graph(model, resume_store=store, checkpointer=MemorySaver())
    return TestClient(create_app(dependencies=AppDependencies(graph=graph)))


def _client_with_business_db(tmp_path: Path, **kwargs: Any) -> tuple[TestClient, Path]:
    """创建带业务 SQLite Session 的测试客户端。"""

    database_path = tmp_path / "app.sqlite3"
    engine = create_sqlalchemy_engine(f"sqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(engine)
    responses = [_route("jd_parse", kwargs.get("task_queue") or ["jd_parse", "resume_match"]), kwargs.get("jd_response", JD_PARSED_JSON)]
    if kwargs.get("task_queue") != ["jd_parse"]:
        responses.append(_match_analysis(kwargs.get("responsibility_relevance", 0.4286)))
    model = FakeChatModel(responses + (kwargs.get("follow_up_responses") or []))
    store = FakeResumeStore(
        {
            ("Java", "2026-07-v1"): [{"chunk_id": "java-1", "quote": "Java", "relevance": 1.0}],
            ("API design", "2026-07-v1"): [
                {
                    "chunk_id": "api-1",
                    "quote": f"API design {kwargs.get('responsibility_relevance', 0.4286)}",
                    "relevance": kwargs.get("responsibility_relevance", 0.4286),
                }
            ],
            ("3年以上后端开发经验", "2026-07-v1"): [{"chunk_id": "exp-1", "quote": "3 years", "relevance": 1.0}],
            ("Java", "2026-07-v2"): [{"chunk_id": "java-1", "quote": "Java", "relevance": 1.0}],
            ("API design", "2026-07-v2"): [{"chunk_id": "api-1", "quote": "API design 1.0", "relevance": 1.0}],
            ("3年以上后端开发经验", "2026-07-v2"): [{"chunk_id": "exp-1", "quote": "3 years", "relevance": 1.0}],
        },
        missing_versions=kwargs.get("missing_versions") or set(),
    )
    graph = build_graph(model, resume_store=store, checkpointer=MemorySaver())
    dependencies = AppDependencies(
        graph=graph,
        session_factory=build_session_factory(engine),
        close=engine.dispose,
    )
    client = TestClient(create_app(dependencies=dependencies))
    return client, database_path


@dataclass
class BlockingGraph:
    release: threading.Event
    started: threading.Event

    def invoke(self, state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        del state, config
        self.started.set()
        self.release.wait(timeout=1)
        return {"final_output": {"type": "jd_parsed"}}


@dataclass
class FailingGraph:
    def invoke(self, state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        del state, config
        raise RuntimeError("boom")


@dataclass
class CompletedGraph:
    def invoke(self, state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        del state, config
        return {"final_output": {"type": "jd_parsed"}}


def test_job_analysis_parses_jd_through_http_boundary() -> None:
    with _client_for(task_queue=["jd_parse"]) as client:
        response = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT})

    assert response.status_code == 200
    payload = response.json()
    assert payload["jd_parsed"]["job_title"] == "Java后端工程师"
    assert payload["match_result"] is None
    assert payload["error_log"] == []
    assert payload["current_node"] == "prepare_final_review"
    assert payload["status"] == "interrupted"
    assert payload["interrupt"]["type"] == "final_review"


@pytest.mark.asyncio
async def test_api_tasks_returns_202_without_waiting_for_graph_completion() -> None:
    release = threading.Event()
    started = threading.Event()
    graph = BlockingGraph(release=release, started=started)
    app = create_app(dependencies=AppDependencies(graph=graph))

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            started_at = time.perf_counter()
            response = await asyncio.wait_for(client.post("/api/tasks", json={"jd_text": JD_TEXT}), timeout=0.2)
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            assert started.wait(timeout=1) is True
            release.set()

    payload = response.json()
    assert response.status_code == 202
    assert payload["status"] == "accepted"
    assert UUID(payload["session_id"]).version == 4
    assert UUID(payload["thread_id"]).version == 4
    assert elapsed_ms < 200


def _sse_endpoint(app: Any) -> Any:
    """取得 session SSE 路由端点，供持续响应测试直接消费迭代器。"""

    return next(
        route.endpoint
        for route in app.routes
        if getattr(route, "path", None) == "/api/sessions/{session_id}/events"
    )


def _sse_request() -> Request:
    """创建保持连接状态的最小 ASGI 请求，供 SSE 端点检测断连。"""

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request({"type": "http", "method": "GET", "path": "/", "headers": []}, receive=receive)


async def _async_read_sse_until(app: Any, session_id: str, marker: str) -> tuple[StreamingResponse, str]:
    """直接消费 SSE 响应直到出现标记，不等待 session 级长连接自然结束。"""

    response = await _sse_endpoint(app)(session_id, _sse_request())
    assert isinstance(response, StreamingResponse)
    body_parts: list[str] = []
    while True:
        chunk = await asyncio.wait_for(anext(response.body_iterator), timeout=1)
        body_parts.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
        current = "".join(body_parts)
        if marker in current:
            return response, current


@pytest.mark.asyncio
async def test_api_tasks_sse_replays_node_lifecycle_events_for_same_thread() -> None:
    app = _client_for(task_queue=["jd_parse"]).app
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            accepted = await client.post("/api/tasks", json={"jd_text": JD_TEXT})
            session_id = accepted.json()["session_id"]
            thread_id = accepted.json()["thread_id"]
        response, body = await _async_read_sse_until(app, session_id, '"event":"node_finished"')
        await response.body_iterator.aclose()

    assert accepted.status_code == 202
    assert f'"event":"node_started"' in body
    assert f'"event":"node_finished"' in body
    assert f'"thread_id":"{thread_id}"' in body


@pytest.mark.asyncio
async def test_api_tasks_sse_receives_run_failed_when_graph_raises() -> None:
    app = create_app(dependencies=AppDependencies(graph=FailingGraph()))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            accepted = await client.post("/api/tasks", json={"jd_text": JD_TEXT})
            session_id = accepted.json()["session_id"]
        response, body = await _async_read_sse_until(app, session_id, '"event":"run_failed"')
        await response.body_iterator.aclose()

    assert accepted.status_code == 202
    assert '"event":"run_failed"' in body


@pytest.mark.asyncio
async def test_api_tasks_sse_receives_run_completed_when_final_output_exists() -> None:
    app = create_app(dependencies=AppDependencies(graph=CompletedGraph()))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            accepted = await client.post("/api/tasks", json={"jd_text": JD_TEXT})
            session_id = accepted.json()["session_id"]
        response, body = await _async_read_sse_until(app, session_id, '"event":"run_completed"')
        await response.body_iterator.aclose()

    assert accepted.status_code == 202
    assert '"event":"run_completed"' in body


def test_job_analysis_generates_uuidv4_session_and_returns_it() -> None:
    with _client_for(task_queue=["jd_parse"]) as client:
        response = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT})

    payload = response.json()
    assert response.status_code == 200
    assert UUID(payload["session_id"]).version == 4
    assert UUID(payload["thread_id"]).version == 4
    assert payload["session_id"] != payload["thread_id"]


def test_job_analysis_reuses_valid_header_session_across_two_threads() -> None:
    session_id = "00000000-0000-4000-8000-000000000001"
    with _client_for(task_queue=["jd_parse"]) as client:
        first = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT}, headers={"X-Session-ID": session_id})
        second = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT}, headers={"X-Session-ID": session_id})

    assert first.status_code == second.status_code == 200
    assert first.json()["session_id"] == second.json()["session_id"] == session_id
    assert first.json()["thread_id"] != second.json()["thread_id"]


def test_job_analysis_rejects_non_uuidv4_session_header() -> None:
    with _client_for(task_queue=["jd_parse"]) as client:
        response = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT}, headers={"X-Session-ID": "not-a-uuid"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SESSION_ID_INVALID"


def test_resume_recovers_checkpoint_session_without_client_header() -> None:
    session_id = "00000000-0000-4000-8000-000000000002"
    with _client_for(task_queue=["jd_parse"]) as client:
        initial = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT}, headers={"X-Session-ID": session_id})
        response = client.post(f"/v1/threads/{initial.json()['thread_id']}/resume", json={"action": "approve"})

    assert response.status_code == 200
    assert response.json()["session_id"] == session_id


def test_thread_state_returns_current_interrupt_for_refresh_recovery() -> None:
    with _client_for(task_queue=["jd_parse"]) as client:
        initial = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT})
        thread_id = initial.json()["thread_id"]
        response = client.get(f"/v1/threads/{thread_id}/state")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "interrupted"
    assert payload["review_status"] == "in_review"
    assert payload["interrupt"]["type"] == "final_review"
    assert payload["interrupt"]["accepted_actions"] == ["approve", "reject"]


def test_thread_state_returns_completed_checkpoint_without_interrupt() -> None:
    with _client_for(task_queue=["jd_parse"]) as client:
        initial = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT})
        thread_id = initial.json()["thread_id"]
        client.post(f"/v1/threads/{thread_id}/resume", json={"action": "approve"})
        response = client.get(f"/v1/threads/{thread_id}/state")

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["interrupt"] is None


def test_thread_state_rejects_unknown_thread() -> None:
    with _client_for() as client:
        response = client.get("/v1/threads/not-found/state")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CHECKPOINT_NOT_FOUND"


def test_resume_persists_review_audit_for_approve_action(tmp_path: Path) -> None:
    client, database_path = _client_with_business_db(tmp_path, task_queue=["jd_parse"])
    with client:
        initial = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT})
        thread_id = initial.json()["thread_id"]
        session_id = initial.json()["session_id"]
        response = client.post(f"/v1/threads/{thread_id}/resume", json={"action": "approve"})

    assert response.status_code == 200
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT session_id, thread_id, review_target, action, result FROM review_audits")
            ).mappings().one()
    finally:
        engine.dispose()

    assert row["session_id"] == session_id
    assert row["thread_id"] == thread_id
    assert row["review_target"] == "jd_parsed"
    assert row["action"] == "approve"
    assert row["result"] == "succeeded"


def test_resume_invalid_command_does_not_persist_review_audit(tmp_path: Path) -> None:
    client, database_path = _client_with_business_db(tmp_path, task_queue=["jd_parse"])
    with client:
        initial = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT})
        thread_id = initial.json()["thread_id"]
        response = client.post(f"/v1/threads/{thread_id}/resume", json={"action": "reject"})

    assert response.status_code == 422
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.connect() as connection:
            count = connection.execute(text("SELECT COUNT(*) FROM review_audits")).scalar_one()
    finally:
        engine.dispose()

    assert count == 0


def test_job_analysis_combines_jd_parse_and_resume_match() -> None:
    with _client_for() as client:
        initial = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT, "resume_version": "2026-07-v1"})
        thread_id = initial.json()["thread_id"]
        response = client.post(f"/v1/threads/{thread_id}/resume", json={"action": "approve"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["jd_parsed"]["job_title"] == "Java后端工程师"
    assert initial.json()["review_target"] == "jd_parsed"
    assert payload["match_result"]["total_score"] >= 60.0
    assert payload["current_node"] == "prepare_final_review"
    assert payload["final_output"] is None
    assert payload["interrupt"]["type"] == "final_review"


def test_final_review_approval_creates_final_output() -> None:
    with _client_for(task_queue=["jd_parse"]) as client:
        interrupted = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT})
        thread_id = interrupted.json()["thread_id"]
        response = client.post(f"/v1/threads/{thread_id}/resume", json={"action": "approve"})

    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == "completed"
    assert payload["review_status"] == "approved"
    assert payload["current_node"] == "finalize_node"
    assert payload["final_output"]["type"] == "jd_parsed"


def test_final_review_reject_requires_feedback_and_returns_to_revising() -> None:
    with _client_for(task_queue=["jd_parse"], follow_up_responses=[JD_PARSED_JSON]) as client:
        interrupted = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT})
        thread_id = interrupted.json()["thread_id"]
        invalid = client.post(f"/v1/threads/{thread_id}/resume", json={"action": "reject"})
        response = client.post(
            f"/v1/threads/{thread_id}/resume",
            json={"action": "reject", "feedback": "请调整报告"},
        )

    assert invalid.status_code == 422
    payload = response.json()
    assert response.status_code == 200
    assert payload["review_status"] == "in_review"
    assert payload["current_node"] == "prepare_final_review"
    assert payload["final_output"] is None
    details = [event["detail"] for event in payload["execution_history"]]
    assert "final_review_rejected" in details
    assert "revising:jd_parsed" in details


def test_combined_analysis_stops_after_technical_jd_failure() -> None:
    with _client_for(jd_response="not-json") as client:
        response = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT, "resume_version": "2026-07-v1"})

    payload = response.json()
    assert response.status_code == 200
    assert payload["match_result"] is None
    assert payload["current_node"] == "error_node"
    assert "JD_EXTRACTION_UNAVAILABLE" in {entry["code"] for entry in payload["error_log"]}
    assert [event["node"] for event in payload["execution_history"]].count("resume_matcher") == 0


def test_combined_analysis_stops_after_content_insufficient_jd() -> None:
    insufficient_jd = json.dumps(
        {"job_title": "unknown", "seniority": "unknown", "company_name": None, "responsibilities": [], "skills": [], "experience_requirements": [], "education_requirements": [], "interview_focus": [], "company_context": [], "ambiguities": [], "source_language": "zh-CN"}
    )
    with _client_for(jd_response=insufficient_jd) as client:
        response = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT, "resume_version": "2026-07-v1"})

    payload = response.json()
    assert response.status_code == 200
    assert payload["match_result"] is None
    assert payload["current_node"] == "error_node"
    assert "JD_CONTENT_INSUFFICIENT" in {entry["code"] for entry in payload["error_log"]}
    assert [event["node"] for event in payload["execution_history"]].count("resume_matcher") == 0


def test_job_analysis_exposes_low_score_review_status() -> None:
    with _client_for(responsibility_relevance=0.4) as client:
        initial = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT, "resume_version": "2026-07-v1"})
        response = client.post(f"/v1/threads/{initial.json()['thread_id']}/resume", json={"action": "approve"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["match_result"]["total_score"] == 59.9
    assert payload["review_status"] == "in_review"
    assert payload["review_target"] == "match_result"
    assert payload["current_node"] == "prepare_low_score_review"
    assert payload["status"] == "interrupted"
    assert payload["interrupt"]["accepted_actions"] == ["continue", "revise_inputs", "cancel"]
    assert payload["final_output"] is None


def test_resume_low_score_review_cancel_ends_without_finalization() -> None:
    with _client_for(responsibility_relevance=0.4) as client:
        interrupted = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT, "resume_version": "2026-07-v1"})
        thread_id = interrupted.json()["thread_id"]
        client.post(f"/v1/threads/{thread_id}/resume", json={"action": "approve"})
        response = client.post(
            f"/v1/threads/{thread_id}/resume",
            json={"action": "cancel", "feedback": "暂不考虑该岗位"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["review_status"] == "rejected"
    assert payload["current_node"] == "low_score_cancelled"
    assert payload["final_output"] is None


def test_low_score_revise_inputs_accepts_new_resume_version_and_records_second_attempt() -> None:
    with _client_for(
        responsibility_relevance=0.4,
        follow_up_responses=[_match_analysis(1.0)],
    ) as client:
        interrupted = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT, "resume_version": "2026-07-v1"})
        thread_id = interrupted.json()["thread_id"]
        client.post(f"/v1/threads/{thread_id}/resume", json={"action": "approve"})
        response = client.post(
            f"/v1/threads/{thread_id}/resume",
            json={"action": "revise_inputs", "resume_version": "2026-07-v2", "feedback": "改用最新简历"},
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == "interrupted"
    assert payload["interrupt"]["type"] == "final_review"
    assert payload["match_result"]["resume_version"] == "2026-07-v2"
    assert payload["match_result"]["total_score"] >= 60.0
    assert payload["final_output"] is None
    matcher_events = [event for event in payload["execution_history"] if event["node"] == "resume_matcher" and event["event"] == "success"]
    assert [event["metadata"]["business_attempt"] for event in matcher_events] == [1, 2]
    assert [event["metadata"]["resume_version"] for event in matcher_events] == ["2026-07-v1", "2026-07-v2"]


def test_low_score_revise_inputs_with_jd_text_reruns_jd_before_second_match() -> None:
    revised_jd_text = "Java后端工程师岗位，要求熟悉 Java、Spring Boot，并具备三年以上接口设计经验。"
    with _client_for(
        responsibility_relevance=0.4,
        follow_up_responses=[JD_PARSED_JSON, _match_analysis(0.4)],
    ) as client:
        interrupted = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT, "resume_version": "2026-07-v1"})
        thread_id = interrupted.json()["thread_id"]
        client.post(f"/v1/threads/{thread_id}/resume", json={"action": "approve"})
        response = client.post(
            f"/v1/threads/{thread_id}/resume",
            json={"action": "revise_inputs", "jd_text": revised_jd_text, "feedback": "补充岗位要求"},
        )
        payload = response.json()
        assert response.status_code == 200
        assert payload["interrupt"]["type"] == "low_match_score"
        assert payload["match_result"]["resume_version"] == "2026-07-v1"
        history_nodes = [event["node"] for event in payload["execution_history"]]
        assert history_nodes.count("jd_parser") >= 1
        matcher_events = [event for event in payload["execution_history"] if event["node"] == "resume_matcher" and event["event"] == "success"]
        assert [event["metadata"]["business_attempt"] for event in matcher_events] == [1, 2]


def test_resume_rejects_unknown_thread() -> None:
    with _client_for() as client:
        response = client.post("/v1/threads/not-found/resume", json={"action": "continue"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CHECKPOINT_NOT_FOUND"


def test_mock_interview_mixed_queue_is_preserved_by_api() -> None:
    with _client_for(task_queue=["mock_interview", "resume_match"]) as client:
        response = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT, "resume_version": "2026-07-v1"})

    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == "interrupted"
    assert payload["interrupt"]["type"] == "interview_answer"
    assert payload["match_result"] is None
    assert payload["interview_state"]["status"] == "waiting"
    assert payload["error_log"] == []
    assert payload["execution_history"][0]["detail"] == "dispatch:mock_interview"


def test_mock_interview_context_update_reinterrupts_same_question() -> None:
    with _client_for(task_queue=["mock_interview"]) as client:
        interrupted = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT})
        thread_id = interrupted.json()["thread_id"]
        response = client.post(
            f"/v1/threads/{thread_id}/resume",
            json={"action": "context_update", "context": "项目峰值QPS是1200"},
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == "interrupted"
    assert payload["interrupt"]["type"] == "interview_answer"
    assert payload["interview_state"]["status"] == "waiting"
    assert payload["interview_state"]["user_context_updates"][-1] == "项目峰值QPS是1200"


def test_mock_interview_submit_answer_evaluates_and_waits_for_next_question() -> None:
    with _client_for(task_queue=["mock_interview"]) as client:
        interrupted = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT})
        thread_id = interrupted.json()["thread_id"]
        response = client.post(
            f"/v1/threads/{thread_id}/resume",
            json={"action": "submit_answer", "answer": "我负责过缓存优化项目。"},
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == "interrupted"
    assert payload["interview_state"]["status"] == "waiting"
    assert payload["interview_state"]["question_records"][0]["answer"] == "我负责过缓存优化项目。"


def test_mock_interview_end_generates_report_and_enters_final_review() -> None:
    with _client_for(task_queue=["mock_interview"]) as client:
        interrupted = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT})
        thread_id = interrupted.json()["thread_id"]
        response = client.post(
            f"/v1/threads/{thread_id}/resume",
            json={"action": "end_interview"},
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == "interrupted"
    assert payload["interview_state"]["status"] == "completed"
    assert payload["interview_state"]["current_question_id"] is None
    assert payload["interview_state"]["report"] is not None
    assert payload["review_status"] == "in_review"
    assert payload["review_target"] == "interview_report"
    assert payload["interrupt"]["type"] == "final_review"
    assert payload["final_output"] is None


def test_mock_interview_report_approval_finalizes_only_report_and_second_approve_is_not_found() -> None:
    with _client_for(task_queue=["mock_interview"]) as client:
        interrupted = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT})
        thread_id = interrupted.json()["thread_id"]
        review = client.post(f"/v1/threads/{thread_id}/resume", json={"action": "end_interview"})
        approved = client.post(f"/v1/threads/{thread_id}/resume", json={"action": "approve"})
        repeated = client.post(f"/v1/threads/{thread_id}/resume", json={"action": "approve"})

    assert review.json()["final_output"] is None
    payload = approved.json()
    assert approved.status_code == 200
    assert payload["status"] == "completed"
    assert payload["final_output"]["type"] == "interview_report"
    assert payload["final_output"]["content"] == payload["interview_state"]["report"]
    assert "question_records" not in payload["final_output"]["content"]
    assert repeated.status_code == 404
    assert repeated.json()["error"]["code"] == "CHECKPOINT_NOT_FOUND"


def test_mock_interview_report_reject_preserves_answer_records_and_reinterrupts() -> None:
    with _client_for(task_queue=["mock_interview"]) as client:
        interrupted = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT})
        thread_id = interrupted.json()["thread_id"]
        client.post(f"/v1/threads/{thread_id}/resume", json={"action": "submit_answer", "answer": "我负责过缓存优化项目。"})
        review = client.post(f"/v1/threads/{thread_id}/resume", json={"action": "end_interview"})
        records_before = review.json()["interview_state"]["question_records"]
        revised = client.post(f"/v1/threads/{thread_id}/resume", json={"action": "reject", "feedback": "请补充行动建议"})

    payload = revised.json()
    assert revised.status_code == 200
    assert payload["status"] == "interrupted"
    assert payload["review_status"] == "in_review"
    assert payload["interrupt"]["type"] == "final_review"
    assert payload["interview_state"]["question_records"] == records_before
    assert payload["final_output"] is None


def test_jd_then_interview_consumes_queue_only_after_jd_approval() -> None:
    with _client_for(task_queue=["jd_parse", "mock_interview"]) as client:
        initial = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT})
        thread_id = initial.json()["thread_id"]
        started_interview = client.post(f"/v1/threads/{thread_id}/resume", json={"action": "approve"})

    assert initial.json()["review_target"] == "jd_parsed"
    assert initial.json()["final_output"] is None
    payload = started_interview.json()
    assert payload["interrupt"]["type"] == "interview_answer"
    assert payload["final_output"] is None


def test_interview_then_jd_preserves_order_and_report_reject_does_not_consume_queue() -> None:
    with _client_for(task_queue=["mock_interview", "jd_parse"]) as client:
        initial = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT})
        thread_id = initial.json()["thread_id"]
        review = client.post(f"/v1/threads/{thread_id}/resume", json={"action": "end_interview"})
        rejected = client.post(f"/v1/threads/{thread_id}/resume", json={"action": "reject", "feedback": "重写报告"})
        approved = client.post(f"/v1/threads/{thread_id}/resume", json={"action": "approve"})

    assert initial.json()["interrupt"]["type"] == "interview_answer"
    assert review.json()["final_output"] is None
    assert rejected.json()["interrupt"]["type"] == "final_review"
    assert rejected.json()["jd_parsed"] is None
    assert approved.json()["review_target"] == "jd_parsed"
    assert approved.json()["final_output"] is None


def test_interview_in_middle_returns_to_remaining_match_only_after_report_approval() -> None:
    with _client_for(task_queue=["jd_parse", "mock_interview", "resume_match"]) as client:
        initial = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT, "resume_version": "2026-07-v1"})
        thread_id = initial.json()["thread_id"]
        interview = client.post(f"/v1/threads/{thread_id}/resume", json={"action": "approve"})
        review = client.post(f"/v1/threads/{thread_id}/resume", json={"action": "end_interview"})
        match = client.post(f"/v1/threads/{thread_id}/resume", json={"action": "approve"})

    assert interview.json()["interrupt"]["type"] == "interview_answer"
    assert review.json()["final_output"] is None
    assert match.json()["review_target"] == "match_result"
    assert match.json()["final_output"] is None


def test_jd_match_low_score_continue_then_interview_preserves_order() -> None:
    with _client_for(task_queue=["jd_parse", "resume_match", "mock_interview"], responsibility_relevance=0.4) as client:
        initial = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT, "resume_version": "2026-07-v1"})
        thread_id = initial.json()["thread_id"]
        low_score = client.post(f"/v1/threads/{thread_id}/resume", json={"action": "approve"})
        match_review = client.post(f"/v1/threads/{thread_id}/resume", json={"action": "continue"})
        interview = client.post(f"/v1/threads/{thread_id}/resume", json={"action": "approve"})

    assert initial.json()["review_target"] == "jd_parsed"
    assert low_score.json()["interrupt"]["type"] == "low_match_score"
    assert match_review.json()["review_target"] == "match_result"
    assert match_review.json()["final_output"] is None
    assert interview.json()["interrupt"]["type"] == "interview_answer"
    assert interview.json()["final_output"] is None


def test_combined_queue_emits_final_output_only_after_last_approval_and_duplicate_resume_is_not_found() -> None:
    with _client_for(task_queue=["mock_interview", "jd_parse"]) as client:
        initial = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT})
        thread_id = initial.json()["thread_id"]
        review = client.post(f"/v1/threads/{thread_id}/resume", json={"action": "end_interview"})
        jd_review = client.post(f"/v1/threads/{thread_id}/resume", json={"action": "approve"})
        completed = client.post(f"/v1/threads/{thread_id}/resume", json={"action": "approve"})
        if completed.status_code != 200 or completed.json().get("final_output") is None:
            completed = client.post(f"/v1/threads/{thread_id}/resume", json={"action": "approve"})
        duplicate = client.post(f"/v1/threads/{thread_id}/resume", json={"action": "approve"})

    assert review.json()["final_output"] is None
    assert jd_review.json()["final_output"] is None
    assert completed.json()["final_output"]["type"] == "jd_parsed"
    assert duplicate.status_code == 404
    assert duplicate.json()["error"]["code"] == "CHECKPOINT_NOT_FOUND"


def test_job_analysis_rejects_empty_and_oversized_text_without_500() -> None:
    with _client_for() as client:
        empty_response = client.post("/v1/job-analysis", json={"jd_text": "   "})
        oversized_response = client.post("/v1/job-analysis", json={"jd_text": "x" * (MAX_INPUT_LENGTH + 1)})

    assert empty_response.status_code == 422
    assert empty_response.json()["error"]["code"] == "INPUT_EMPTY"
    assert oversized_response.status_code == 422
    assert oversized_response.json()["error"]["code"] == "INPUT_TOO_LONG"


def test_job_analysis_exposes_missing_resume_version_without_500() -> None:
    with _client_for(missing_versions={"missing-v1"}) as client:
        initial = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT, "resume_version": "missing-v1"})
        response = client.post(f"/v1/threads/{initial.json()['thread_id']}/resume", json={"action": "approve"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["jd_parsed"]["job_title"] == "Java后端工程师"
    assert payload["match_result"] is None
    assert payload["error_log"][0]["code"] == "RESUME_VERSION_NOT_FOUND"