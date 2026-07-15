"""Supervisor 节点测试。"""

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.agents.supervisor import supervisor_node
from app.constants import MAX_INPUT_LENGTH
from app.schemas.router import RouterDecision


@dataclass
class FakeChatModel:
    result: Any
    invoke_calls: int = 0
    prompts: list[str] = field(default_factory=list)

    def invoke(self, prompt: str) -> Any:
        self.invoke_calls += 1
        self.prompts.append(prompt)
        return self.result

    def bind(self, **_: Any) -> "FakeChatModel":
        return self


@pytest.mark.core_agent_tests
def test_supervisor_routes_jd_request_correctly() -> None:
    model = FakeChatModel('{"route":"jd_parse","confidence":0.9,"reason":"JD analysis request","task_queue":[]}')

    result = supervisor_node({"user_input": "分析这个JD"}, model)

    assert model.invoke_calls == 1
    assert result["route_decision"].route == "jd_parse"


@pytest.mark.core_agent_tests
def test_supervisor_preserves_combined_task_order() -> None:
    model = FakeChatModel(
        '{"route":"jd_parse","confidence":0.95,"reason":"Two tasks requested","task_queue":["jd_parse","resume_match"]}'
    )

    result = supervisor_node({"user_input": "分析这个JD并帮我匹配简历"}, model)

    assert result["task_queue"] == ["jd_parse", "resume_match"]


@pytest.mark.core_agent_tests
def test_supervisor_normalizes_mixed_interview_queue_with_structured_audit() -> None:
    model = FakeChatModel(
        '{"route":"mock_interview","confidence":0.95,"reason":"Interview plus match requested","task_queue":["mock_interview","resume_match"]}'
    )

    result = supervisor_node({"user_input": "先开始面试再帮我匹配简历"}, model)

    decision: RouterDecision = result["route_decision"]
    assert decision.route == "mock_interview"
    assert result["task_queue"] == ["mock_interview"]
    assert result["execution_history"][0]["detail"] == "interview_queue_normalized"
    assert result["execution_history"][0]["metadata"]["original_task_queue"] == ["mock_interview", "resume_match"]
    assert result["execution_history"][0]["metadata"]["normalized_task_queue"] == ["mock_interview"]
    assert result["error_log"][0]["code"] == "INTERVIEW_QUEUE_NORMALIZED"


@pytest.mark.core_agent_tests
def test_supervisor_out_of_scope_does_not_call_any_worker() -> None:
    model = FakeChatModel('{"route":"out_of_scope","confidence":0.92,"reason":"Poem request","task_queue":[]}')

    result = supervisor_node({"user_input": "帮我写一首诗"}, model)

    assert model.invoke_calls == 1
    assert result["route_decision"].route == "out_of_scope"


@pytest.mark.core_agent_tests
def test_supervisor_empty_input_does_not_call_llm() -> None:
    model = FakeChatModel('{"route":"jd_parse","confidence":0.9,"reason":"should not be used","task_queue":[]}')

    result = supervisor_node({"user_input": "   "}, model)

    assert model.invoke_calls == 0
    assert result["route_decision"].route == "clarify"
    assert result["error_log"][0]["code"] == "INPUT_EMPTY"


@pytest.mark.core_agent_tests
def test_supervisor_rejects_input_too_long_without_truncation() -> None:
    oversized_input = "x" * (MAX_INPUT_LENGTH + 1)
    model = FakeChatModel('{"route":"jd_parse","confidence":0.9,"reason":"should not be used","task_queue":[]}')

    result = supervisor_node({"user_input": oversized_input}, model)

    assert model.invoke_calls == 0
    assert result["route_decision"] is None
    assert result["error_log"][0]["code"] == "INPUT_TOO_LONG"
    assert str(MAX_INPUT_LENGTH) in result["error_log"][0]["message"]


@pytest.mark.core_agent_tests
def test_supervisor_low_confidence_forces_clarify() -> None:
    model = FakeChatModel('{"route":"resume_match","confidence":0.69,"reason":"Ambiguous request","task_queue":["resume_match"]}')

    result = supervisor_node({"user_input": "帮我看看这个岗位"}, model)

    decision: RouterDecision = result["route_decision"]
    assert decision.route == "clarify"
    assert decision.confidence == 0.69
    assert result["task_queue"] == []
