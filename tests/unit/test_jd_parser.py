"""JD 解析 Agent 测试。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import ValidationError

from app.agents.jd_parser import (
    CONTENT_INSUFFICIENT_CODE,
    EXTRACTION_UNAVAILABLE_CODE,
    WEB_SEARCH_DEGRADED_CODE,
    jd_parser_node,
)
from app.schemas.jd import JDParseInput


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


def test_jd_parser_extracts_skills_with_original_evidence() -> None:
    jd_text = "某后端岗位，要求熟悉 Python、FastAPI，3年以上后端开发经验，负责接口设计与性能优化。"
    model = FakeChatModel(
        [
            """{"job_title":"后端工程师","seniority":"mid","company_name":null,"responsibilities":["负责接口设计与性能优化"],"skills":[{"name":"Python","category":"language","priority":"must","evidence":"熟悉 Python"},{"name":"FastAPI","category":"framework","priority":"must","evidence":"FastAPI"}],"experience_requirements":["3年以上后端开发经验"],"education_requirements":[],"interview_focus":["接口设计","性能优化"],"company_context":[],"ambiguities":[],"source_language":"zh-CN"}"""
        ]
    )

    result = jd_parser_node({"user_input": jd_text}, model)
    jd_parsed = result["jd_parsed"]

    assert {skill.name for skill in jd_parsed.skills} == {"Python", "FastAPI"}
    assert all(skill.evidence in jd_text for skill in jd_parsed.skills)
    assert jd_parsed.experience_requirements == ["3年以上后端开发经验"]
    assert result["retry_count"] == {"jd_parser": 0}


def test_jd_parser_without_company_name_never_calls_search() -> None:
    jd_text = "某后端岗位，要求熟悉 Python、FastAPI，3年以上后端开发经验，负责接口设计与性能优化。"
    model = FakeChatModel(
        [
            """{"job_title":"后端工程师","seniority":"mid","company_name":null,"responsibilities":["负责接口设计与性能优化"],"skills":[{"name":"Python","category":"language","priority":"must","evidence":"熟悉 Python"}],"experience_requirements":["3年以上后端开发经验"],"education_requirements":[],"interview_focus":[],"company_context":["should-be-cleared"],"ambiguities":[],"source_language":"zh-CN"}"""
        ]
    )
    calls = {"count": 0}

    def backend(_: str, __: int) -> list[object]:
        calls["count"] += 1
        return []

    result = jd_parser_node({"user_input": jd_text}, model, backend)
    jd_parsed = result["jd_parsed"]

    assert calls["count"] == 0
    assert jd_parsed.company_name is None
    assert jd_parsed.company_context == []


def test_jd_parser_without_authorization_never_calls_search() -> None:
    jd_text = "字节跳动后端岗位，要求熟悉 Python，负责接口设计与性能优化，3年以上后端开发经验。"
    model = FakeChatModel(
        [
            """{"job_title":"后端工程师","seniority":"mid","company_name":"字节跳动","responsibilities":["负责接口设计与性能优化"],"skills":[{"name":"Python","category":"language","priority":"must","evidence":"熟悉 Python"}],"experience_requirements":["3年以上后端开发经验"],"education_requirements":[],"interview_focus":[],"company_context":[],"ambiguities":[],"source_language":"zh-CN"}"""
        ]
    )
    calls = {"count": 0}

    def backend(_: str, __: int) -> list[object]:
        calls["count"] += 1
        return []

    result = jd_parser_node({"user_input": jd_text}, model, backend)

    assert calls["count"] == 0
    assert result["jd_parsed"].company_context == []


def test_jd_parser_corrects_preferred_skill_from_priority_misclassification() -> None:
    jd_text = "某后端岗位，了解 Kubernetes 优先，熟悉 Python，负责接口设计与性能优化。"
    model = FakeChatModel(
        [
            """{"job_title":"后端工程师","seniority":"mid","company_name":null,"responsibilities":["负责接口设计与性能优化"],"skills":[{"name":"Kubernetes","category":"cloud","priority":"must","evidence":"了解 Kubernetes 优先"},{"name":"Python","category":"language","priority":"must","evidence":"熟悉 Python"}],"experience_requirements":[],"education_requirements":[],"interview_focus":[],"company_context":[],"ambiguities":[],"source_language":"zh-CN"}"""
        ]
    )

    result = jd_parser_node({"user_input": jd_text}, model)
    priorities = {skill.name: skill.priority for skill in result["jd_parsed"].skills}

    assert priorities["Kubernetes"] == "preferred"
    assert priorities["Python"] == "must"


def test_jd_parser_does_not_fabricate_skills_for_content_insufficient_jd() -> None:
    jd_text = "我们提供开放办公环境、弹性福利、成长空间，欢迎加入优秀团队一起创造价值。"
    model = FakeChatModel(
        [
            """{"job_title":"unknown","seniority":"unknown","company_name":null,"responsibilities":[],"skills":[],"experience_requirements":[],"education_requirements":[],"interview_focus":[],"company_context":[],"ambiguities":[],"source_language":"zh-CN"}"""
        ]
    )

    result = jd_parser_node({"user_input": jd_text}, model)
    jd_parsed = result["jd_parsed"]

    assert jd_parsed.skills == []
    assert any(item.startswith(f"{CONTENT_INSUFFICIENT_CODE}:") for item in jd_parsed.ambiguities)
    assert result["execution_history"][0]["detail"] == "content_insufficient"


def test_jd_parser_marks_technical_degradation_separately_from_content_insufficient() -> None:
    jd_text = "职位：后端工程师\n要求熟悉 Python、FastAPI，3年以上后端开发经验，负责接口设计与性能优化。"
    model = FakeChatModel(["Authorization: Bearer sk-sensitive user@example.com " + "x" * 600, "still-not-json", "bad-output"])

    result = jd_parser_node({"user_input": jd_text}, model)
    jd_parsed = result["jd_parsed"]

    assert model.invoke_calls == 3
    assert jd_parsed.job_title == "后端工程师"
    assert jd_parsed.skills == []
    assert any(item.startswith(f"{EXTRACTION_UNAVAILABLE_CODE}:") for item in jd_parsed.ambiguities)
    assert not any(item.startswith(f"{CONTENT_INSUFFICIENT_CODE}:") for item in jd_parsed.ambiguities)
    assert len(result["error_log"]) == 4
    assert result["error_log"][-1]["code"] == EXTRACTION_UNAVAILABLE_CODE
    assert all(entry["code"] == "LLM_SCHEMA_INVALID" for entry in result["error_log"][:-1])
    assert result["error_log"][-1]["retryable"] is False
    assert result["retry_count"] == {"jd_parser": 2}
    assert result["execution_history"][0]["detail"] == "technical_degraded_manual_review_required"
    assert "必须人工核可" in jd_parsed.ambiguities[0]
    first_excerpt = result["error_log"][0]["raw_output_excerpt"]
    assert first_excerpt is not None
    assert "sk-sensitive" not in first_excerpt
    assert "user@example.com" not in first_excerpt
    assert len(first_excerpt) <= 500


def test_jd_parser_writes_only_jd_business_field() -> None:
    jd_text = "某后端岗位，要求熟悉 Python，负责接口设计与性能优化。"
    model = FakeChatModel(
        [
            """{"job_title":"后端工程师","seniority":"mid","company_name":null,"responsibilities":["负责接口设计与性能优化"],"skills":[{"name":"Python","category":"language","priority":"must","evidence":"熟悉 Python"}],"experience_requirements":[],"education_requirements":[],"interview_focus":[],"company_context":[],"ambiguities":[],"source_language":"zh-CN"}"""
        ]
    )

    result = jd_parser_node({"user_input": jd_text}, model)

    assert "jd_parsed" in result
    assert "match_result" not in result
    assert "interview_state" not in result


def test_jd_parse_input_rejects_too_short_text() -> None:
    with pytest.raises(ValidationError):
        JDParseInput(jd_text="太短了")


def test_jd_parser_authorized_company_search_uses_at_most_five_results() -> None:
    jd_text = "字节跳动后端岗位，要求熟悉 Python，负责接口设计与性能优化，3年以上后端开发经验。"
    model = FakeChatModel(
        [
            """{"job_title":"后端工程师","seniority":"mid","company_name":"字节跳动","responsibilities":["负责接口设计与性能优化"],"skills":[{"name":"Python","category":"language","priority":"must","evidence":"熟悉 Python"}],"experience_requirements":["3年以上后端开发经验"],"education_requirements":[],"interview_focus":[],"company_context":[],"ambiguities":[],"source_language":"zh-CN"}"""
        ]
    )
    calls = {"count": 0}

    def backend(_: str, limit: int) -> list[Any]:
        calls["count"] += 1
        assert limit == 5
        return [
            type("Item", (), {"title": f"title-{index}", "url": f"https://example.com/{index}", "snippet": f"snippet-{index}", "fetched_at": f"2026-07-12T00:00:0{index}+00:00"})()
            for index in range(8)
        ]

    result = jd_parser_node(
        {"user_input": jd_text, "allow_web_search": True},
        model,
        backend,
    )

    assert calls["count"] == 1
    assert len(result["jd_parsed"].company_context) == 5


def test_jd_parser_search_failure_degrades_without_affecting_structured_retry_count() -> None:
    jd_text = "字节跳动后端岗位，要求熟悉 Python，负责接口设计与性能优化，3年以上后端开发经验。"
    model = FakeChatModel(
        [
            """{"job_title":"后端工程师","seniority":"mid","company_name":"字节跳动","responsibilities":["负责接口设计与性能优化"],"skills":[{"name":"Python","category":"language","priority":"must","evidence":"熟悉 Python"}],"experience_requirements":["3年以上后端开发经验"],"education_requirements":[],"interview_focus":[],"company_context":[],"ambiguities":[],"source_language":"zh-CN"}"""
        ]
    )
    calls = {"count": 0}

    def backend(_: str, __: int) -> list[Any]:
        calls["count"] += 1
        raise TimeoutError("timeout")

    result = jd_parser_node(
        {"user_input": jd_text, "allow_web_search": True},
        model,
        backend,
    )
    jd_parsed = result["jd_parsed"]

    assert calls["count"] == 2
    assert jd_parsed.job_title == "后端工程师"
    assert [skill.name for skill in jd_parsed.skills] == ["Python"]
    assert jd_parsed.company_context == []
    assert any(entry["code"] == WEB_SEARCH_DEGRADED_CODE for entry in result["error_log"])
    degraded_entry = next(entry for entry in result["error_log"] if entry["code"] == WEB_SEARCH_DEGRADED_CODE)
    assert degraded_entry["attempt"] == 1
    assert degraded_entry["retryable"] is False
    assert result["retry_count"] == {"jd_parser": 0}
    assert result["execution_history"][0]["detail"] == "parsed"
