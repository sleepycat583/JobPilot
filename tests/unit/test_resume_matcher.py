"""简历匹配 Agent 测试。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agents.resume_matcher import MATCH_UNAVAILABLE_CODE, RAG_EMPTY_RESULT_CODE, RAG_EMPTY_RESULT_GAP, resume_matcher_node
from app.rag.chroma_store import ResumeVersionNotFoundError
from app.schemas.jd import JDParsed, SkillRequirement
from app.schemas.resume import MatchUnavailableResult


@dataclass
class FakeChatModel:
    responses: list[Any]
    invoke_calls: int = 0
    prompts: list[str] = field(default_factory=list)

    def invoke(self, prompt: str) -> Any:
        self.prompts.append(prompt)
        response = self.responses[min(self.invoke_calls, len(self.responses) - 1)]
        self.invoke_calls += 1
        return response

    def bind(self, **_: Any) -> "FakeChatModel":
        return self


class FakeResumeStore:
    def __init__(self, mapping: dict[tuple[str, str], list[dict[str, Any]]], missing_versions: set[str] | None = None) -> None:
        self.mapping = mapping
        self.missing_versions = missing_versions or set()
        self.calls: list[tuple[str, str]] = []

    def query(self, query_text: str, resume_version: str) -> list[dict[str, Any]]:
        self.calls.append((query_text, resume_version))
        if resume_version in self.missing_versions:
            raise ResumeVersionNotFoundError(resume_version)
        return self.mapping.get((query_text, resume_version), [])


def build_jd() -> JDParsed:
    return JDParsed(
        job_title="Java后端工程师",
        seniority="mid",
        company_name=None,
        responsibilities=["负责微服务接口设计"],
        skills=[
            SkillRequirement(name="Java", category="language", priority="must", evidence="精通 Java"),
            SkillRequirement(name="Spring Boot", category="framework", priority="must", evidence="熟练 Spring Boot"),
            SkillRequirement(name="Kubernetes", category="cloud", priority="preferred", evidence="熟悉 Kubernetes 优先"),
        ],
        experience_requirements=["3年以上后端开发经验"],
        education_requirements=[],
        interview_focus=[],
        company_context=[],
        ambiguities=[],
        source_language="zh-CN",
    )


def test_resume_matcher_binds_java_and_spring_boot_to_real_chunk_quote() -> None:
    store = FakeResumeStore(
        {
            ("Java", "2026-07-v1"): [
                {"chunk_id": "project-java", "quote": "主导 Java 微服务开发，重构订单系统。", "relevance": 0.93}
            ],
            ("Spring Boot", "2026-07-v1"): [
                {"chunk_id": "project-spring", "quote": "使用 Spring Boot 搭建支付服务与管理后台。", "relevance": 0.91}
            ],
            ("负责微服务接口设计", "2026-07-v1"): [
                {"chunk_id": "project-api", "quote": "负责微服务接口设计与性能优化，沉淀统一 API 规范。", "relevance": 0.9}
            ],
            ("Kubernetes", "2026-07-v1"): [],
            ("3年以上后端开发经验", "2026-07-v1"): [
                {"chunk_id": "experience-3y", "quote": "拥有 4 年 Java 后端开发经验。", "relevance": 0.95}
            ],
        }
    )
    model = FakeChatModel(
        [
            """{"must_items":[{"requirement":"Java","status":"matched","rationale":"具备直接 Java 经验","evidence":[{"chunk_id":"project-java","quote":"主导 Java 微服务开发，重构订单系统。"}],"recent":true,"quantified":false},{"requirement":"Spring Boot","status":"matched","rationale":"具备直接 Spring Boot 经验","evidence":[{"chunk_id":"project-spring","quote":"使用 Spring Boot 搭建支付服务与管理后台。"}],"recent":true,"quantified":false}],"responsibility_items":[{"requirement":"负责微服务接口设计","status":"matched","rationale":"有直接接口设计项目","evidence":[{"chunk_id":"project-api","quote":"负责微服务接口设计与性能优化，沉淀统一 API 规范。"}],"recent":true,"quantified":true}],"preferred_items":[{"requirement":"Kubernetes","status":"missing","rationale":"未找到直接证据","evidence":[],"recent":false,"quantified":false}],"constraint_items":[{"requirement":"3年以上后端开发经验","status":"satisfied","rationale":"年限满足","evidence":[{"chunk_id":"experience-3y","quote":"拥有 4 年 Java 后端开发经验。"}]}],"strengths":["Java/Spring Boot 项目经验直接对口"],"gaps":["缺少 Kubernetes 直接证据"],"recommendations":["补充容器编排项目经历"]}"""
        ]
    )

    result = resume_matcher_node(
        {"jd_parsed": build_jd(), "resume_version": "2026-07-v1", "match_result": None},
        model,
        store,
    )
    match_result = result["match_result"]

    java_item = next(item for item in match_result.matched_items if item.requirement == "Java")
    spring_item = next(item for item in match_result.matched_items if item.requirement == "Spring Boot")
    assert java_item.evidence[0].chunk_id == "project-java"
    assert java_item.evidence[0].quote == "主导 Java 微服务开发，重构订单系统。"
    assert spring_item.evidence[0].chunk_id == "project-spring"
    assert spring_item.evidence[0].quote == "使用 Spring Boot 搭建支付服务与管理后台。"
    assert result["error_log"] == []
    assert result["retry_count"] == {"resume_matcher": 0}


def test_resume_matcher_does_not_treat_docker_as_kubernetes_evidence() -> None:
    store = FakeResumeStore(
        {
            ("Java", "2026-07-v1"): [],
            ("Spring Boot", "2026-07-v1"): [],
            ("负责微服务接口设计", "2026-07-v1"): [],
            ("Kubernetes", "2026-07-v1"): [
                {"chunk_id": "docker-only", "quote": "熟悉 Docker 镜像构建与部署流程。", "relevance": 0.52}
            ],
            ("3年以上后端开发经验", "2026-07-v1"): [],
        }
    )
    model = FakeChatModel(
        [
            """{"must_items":[{"requirement":"Java","status":"missing","rationale":"无证据","evidence":[],"recent":false,"quantified":false},{"requirement":"Spring Boot","status":"missing","rationale":"无证据","evidence":[],"recent":false,"quantified":false}],"responsibility_items":[{"requirement":"负责微服务接口设计","status":"missing","rationale":"无证据","evidence":[],"recent":false,"quantified":false}],"preferred_items":[{"requirement":"Kubernetes","status":"matched","rationale":"模型误把 Docker 当成 Kubernetes","evidence":[{"chunk_id":"docker-only","quote":"Kubernetes"}],"recent":false,"quantified":false}],"constraint_items":[{"requirement":"3年以上后端开发经验","status":"missing","rationale":"无证据","evidence":[]}],"strengths":[],"gaps":["缺少 Kubernetes 直接证据"],"recommendations":["补充 Kubernetes 项目"]}"""
        ]
    )

    result = resume_matcher_node(
        {"jd_parsed": build_jd(), "resume_version": "2026-07-v1"},
        model,
        store,
    )
    kube_item = next(item for item in result["match_result"].matched_items if item.requirement == "Kubernetes")

    assert kube_item.status == "missing"
    assert kube_item.evidence == []


def test_resume_matcher_caps_empty_rag_result_and_logs_code() -> None:
    store = FakeResumeStore({})
    model = FakeChatModel(
        [
            """{"must_items":[{"requirement":"Java","status":"missing","rationale":"无证据","evidence":[],"recent":false,"quantified":false},{"requirement":"Spring Boot","status":"missing","rationale":"无证据","evidence":[],"recent":false,"quantified":false}],"responsibility_items":[{"requirement":"负责微服务接口设计","status":"missing","rationale":"无证据","evidence":[],"recent":false,"quantified":false}],"preferred_items":[{"requirement":"Kubernetes","status":"missing","rationale":"无证据","evidence":[],"recent":false,"quantified":false}],"constraint_items":[{"requirement":"3年以上后端开发经验","status":"satisfied","rationale":"年限满足","evidence":[]}],"strengths":[],"gaps":["所有关键能力均缺少证据"],"recommendations":["重新检查简历索引或人工确认"]}"""
        ]
    )

    result = resume_matcher_node(
        {"jd_parsed": build_jd(), "resume_version": "2026-07-v1"},
        model,
        store,
    )

    assert result["match_result"].total_score <= 10.0
    assert result["match_result"].resume_version == "2026-07-v1"
    assert any(entry["code"] == RAG_EMPTY_RESULT_CODE for entry in result["error_log"])
    assert RAG_EMPTY_RESULT_GAP in result["match_result"].gaps
    assert RAG_EMPTY_RESULT_CODE not in result["match_result"].gaps


def test_resume_matcher_returns_resume_version_not_found_without_fallback() -> None:
    store = FakeResumeStore({}, missing_versions={"missing-v1"})
    model = FakeChatModel(["should-not-be-used"])

    result = resume_matcher_node(
        {"jd_parsed": build_jd(), "resume_version": "missing-v1"},
        model,
        store,
    )

    assert result["match_result"] is None
    assert result["error_log"][0]["code"] == "RESUME_VERSION_NOT_FOUND"
    assert result["execution_history"][0]["detail"] == "resume_version_not_found"
    assert model.invoke_calls == 0
    assert all(version == "missing-v1" for _, version in store.calls)


def test_resume_matcher_returns_unscored_evidence_when_structured_output_retries_exhausted() -> None:
    store = FakeResumeStore(
        {
            ("Java", "2026-07-v1"): [
                {"chunk_id": "project-java", "quote": "主导 Java 微服务开发。", "relevance": 0.93}
            ]
        }
    )
    model = FakeChatModel(
        [
            "Authorization: Bearer sk-sensitive user@example.com " + "x" * 600,
            "still-not-json",
            "bad-output",
        ]
    )

    result = resume_matcher_node(
        {"jd_parsed": build_jd(), "resume_version": "2026-07-v1"},
        model,
        store,
    )
    match_result = result["match_result"]

    assert model.invoke_calls == 3
    assert isinstance(match_result, MatchUnavailableResult)
    assert match_result.status == "MATCH_UNAVAILABLE"
    assert not hasattr(match_result, "total_score")
    java_evidence = next(item for item in match_result.retrieval_evidence if item.requirement == "Java")
    assert java_evidence.evidence[0].chunk_id == "project-java"
    assert any(entry["code"] == MATCH_UNAVAILABLE_CODE for entry in result["error_log"])
    unavailable_entry = next(entry for entry in result["error_log"] if entry["code"] == MATCH_UNAVAILABLE_CODE)
    assert unavailable_entry["attempt"] == 2
    assert unavailable_entry["retryable"] is False
    first_excerpt = result["error_log"][0]["raw_output_excerpt"]
    assert first_excerpt is not None
    assert "sk-sensitive" not in first_excerpt
    assert "user@example.com" not in first_excerpt
    assert len(first_excerpt) <= 500
    assert result["execution_history"][0]["detail"] == "match_unavailable"


def test_resume_matcher_writes_only_match_result_business_field() -> None:
    store = FakeResumeStore({})
    model = FakeChatModel(
        [
            """{"must_items":[{"requirement":"Java","status":"missing","rationale":"无证据","evidence":[],"recent":false,"quantified":false},{"requirement":"Spring Boot","status":"missing","rationale":"无证据","evidence":[],"recent":false,"quantified":false}],"responsibility_items":[{"requirement":"负责微服务接口设计","status":"missing","rationale":"无证据","evidence":[],"recent":false,"quantified":false}],"preferred_items":[{"requirement":"Kubernetes","status":"missing","rationale":"无证据","evidence":[],"recent":false,"quantified":false}],"constraint_items":[{"requirement":"3年以上后端开发经验","status":"missing","rationale":"无证据","evidence":[]}],"strengths":[],"gaps":[],"recommendations":[]}"""
        ]
    )

    result = resume_matcher_node(
        {"jd_parsed": build_jd(), "resume_version": "2026-07-v1", "jd_parsed_extra": "ignored"},
        model,
        store,
    )

    assert "match_result" in result
    assert "jd_parsed" not in result
    assert "interview_state" not in result