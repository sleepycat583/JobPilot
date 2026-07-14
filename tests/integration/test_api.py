"""FastAPI HTTP 与完整 Graph 集成验收。

测试通过 TestClient 调用真实编译 Graph；Chat Model 和简历存储均为 fake，
不访问外部 LLM、Embedding 或 Chroma 服务。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from app.api import AppDependencies, create_app
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
    prompts: list[str] = field(default_factory=list)

    def invoke(self, prompt: str) -> str:
        self.prompts.append(prompt)
        response = self.responses[min(self.invoke_calls, len(self.responses) - 1)]
        self.invoke_calls += 1
        return response

    def bind(self, **_: Any) -> "FakeChatModel":
        return self


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
        },
        missing_versions=missing_versions or set(),
    )
    graph = build_graph(model, resume_store=store, checkpointer=MemorySaver())
    return TestClient(create_app(dependencies=AppDependencies(graph=graph)))


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


def test_job_analysis_combines_jd_parse_and_resume_match() -> None:
    with _client_for() as client:
        response = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT, "resume_version": "2026-07-v1"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["jd_parsed"]["job_title"] == "Java后端工程师"
    assert payload["match_result"]["total_score"] == 60.0
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
        response = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT, "resume_version": "2026-07-v1"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["match_result"]["total_score"] == 59.9
    assert payload["review_status"] == "in_review"
    assert payload["review_target"] == "match_result"
    assert payload["current_node"] == "prepare_low_score_review"
    assert payload["status"] == "interrupted"
    assert payload["interrupt"]["accepted_actions"] == ["continue", "cancel"]
    assert payload["final_output"] is None


def test_resume_low_score_review_cancel_ends_without_finalization() -> None:
    with _client_for(responsibility_relevance=0.4) as client:
        interrupted = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT, "resume_version": "2026-07-v1"})
        thread_id = interrupted.json()["thread_id"]
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


def test_resume_rejects_unknown_thread() -> None:
    with _client_for() as client:
        response = client.post("/v1/threads/not-found/resume", json={"action": "continue"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CHECKPOINT_NOT_FOUND"


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
        response = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT, "resume_version": "missing-v1"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["jd_parsed"]["job_title"] == "Java后端工程师"
    assert payload["match_result"] is None
    assert payload["error_log"][0]["code"] == "RESUME_VERSION_NOT_FOUND"