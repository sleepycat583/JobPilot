"""JD 节点接入 Graph 后的基本行为测试。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.graph.builder import build_graph
from app.schemas.jd import JDParsed


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

    def with_structured_output(self, schema: type[JDParsed]) -> "StructuredFakeChatModel":
        assert schema is JDParsed
        return StructuredFakeChatModel(self, schema)


@dataclass
class StructuredFakeChatModel:
    parent: FakeChatModel
    schema: type[JDParsed]

    def invoke(self, prompt: str) -> JDParsed:
        return self.schema.model_validate_json(self.parent.invoke(prompt))


def test_graph_uses_real_jd_parser_node_output() -> None:
    graph = build_graph(
        FakeChatModel(
            [
                '{"route":"jd_parse","confidence":0.9,"reason":"jd","task_queue":[]}',
                '{"job_title":"后端工程师","seniority":"mid","company_name":null,"responsibilities":["负责接口设计与性能优化"],"skills":[{"name":"Python","category":"language","priority":"must","evidence":"熟悉 Python"}],"experience_requirements":[],"education_requirements":[],"interview_focus":[],"company_context":[],"ambiguities":[],"source_language":"zh-CN"}',
            ]
        )
    )

    result = graph.invoke({"user_input": "某后端岗位，要求熟悉 Python，负责接口设计与性能优化。"})

    assert result["current_node"] == "prepare_final_review"
    assert result["review_status"] == "in_review"
    assert result["jd_parsed"].job_title == "后端工程师"


def test_graph_returns_degraded_jd_to_supervisor_for_clarification() -> None:
    graph = build_graph(
        FakeChatModel(
            [
                '{"route":"jd_parse","confidence":0.9,"reason":"jd","task_queue":[]}',
                "invalid-jd-tool-call",
                "invalid-jd-tool-call-again",
                '{"route":"clarify","confidence":0.9,"reason":"需要完整JD","task_queue":[]}',
            ]
        )
    )

    result = graph.invoke({"user_input": "某后端岗位，要求熟悉 Python，负责接口设计与性能优化。"})

    assert result["current_node"] == "clarify_node"
    assert result["jd_parsed"].job_title == "unknown"
    assert result["jd_parsed"].seniority == "unknown"
    assert result["route_decision"].route == "clarify"
    assert any(entry["code"] == "JD_EXTRACTION_UNAVAILABLE" for entry in result["error_log"])