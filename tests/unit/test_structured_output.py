"""结构化输出重试服务测试。"""

from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import BaseModel, Field

from app.services.structured_output import StructuredPromptContext, call_with_structured_output


class DemoSchema(BaseModel):
    value: int = Field(ge=0)
    label: str


@dataclass
class FakeResponse:
    content: str


@dataclass
class FakeBoundModel:
    responses: list[Any]
    prompts: list[str]
    call_count_ref: dict[str, int]

    def invoke(self, prompt: str) -> Any:
        self.prompts.append(prompt)
        index = self.call_count_ref["count"]
        self.call_count_ref["count"] += 1
        return self.responses[index]


@dataclass
class FakeChatModel:
    responses: list[Any]
    prompts: list[str] = field(default_factory=list)
    bind_calls: list[dict[str, Any]] = field(default_factory=list)
    call_count_ref: dict[str, int] = field(default_factory=lambda: {"count": 0})

    def invoke(self, prompt: str) -> Any:
        self.prompts.append(prompt)
        index = self.call_count_ref["count"]
        self.call_count_ref["count"] += 1
        return self.responses[index]

    def bind(self, **kwargs: Any) -> FakeBoundModel:
        self.bind_calls.append(kwargs)
        return FakeBoundModel(
            responses=self.responses,
            prompts=self.prompts,
            call_count_ref=self.call_count_ref,
        )


@pytest.mark.core_agent_tests
def test_structured_output_succeeds_after_two_retries() -> None:
    model = FakeChatModel(
        responses=[
            FakeResponse("not json"),
            FakeResponse('{"value": 1}'),
            FakeResponse('{"value": 1, "label": "ok"}'),
        ]
    )
    context = StructuredPromptContext(full_prompt="FULL PROMPT", minimal_input="MINIMAL INPUT")

    result = call_with_structured_output(model, DemoSchema, context, "router")

    assert result.degraded is False
    assert result.retry_count == 2
    assert result.value is not None
    assert result.value.value == 1
    assert result.value.label == "ok"
    assert len(result.error_log) == 2
    assert result.error_log[0]["attempt"] == 0
    assert result.error_log[1]["attempt"] == 1
    assert model.call_count_ref["count"] == 3
    assert "Validation errors" in model.prompts[1]
    assert "JSON Schema" in model.prompts[1]
    assert "Previous output" in model.prompts[1]
    assert "Minimal task input" in model.prompts[2]
    assert model.bind_calls == [{"temperature": 0}]


@pytest.mark.core_agent_tests
def test_structured_output_degrades_after_three_failures() -> None:
    oversized_output = "x" * 800
    model = FakeChatModel(
        responses=[
            FakeResponse("not json"),
            FakeResponse('{"value": 1}'),
            FakeResponse(oversized_output),
        ]
    )
    context = StructuredPromptContext(full_prompt="FULL PROMPT", minimal_input="MINIMAL INPUT")

    result = call_with_structured_output(model, DemoSchema, context, "jd_agent")

    assert result.degraded is True
    assert result.value is None
    assert result.retry_count == 2
    assert len(result.error_log) == 3
    assert result.error_log[-1]["retryable"] is False
    assert result.error_log[-1]["attempt"] == 2
    assert len(result.error_log[-1]["raw_output_excerpt"] or "") == 500
    assert model.call_count_ref["count"] == 3
    assert model.bind_calls == [{"temperature": 0}]


@pytest.mark.core_agent_tests
def test_structured_output_succeeds_on_first_attempt() -> None:
    model = FakeChatModel(responses=[FakeResponse('{"value": 1, "label": "ok"}')])
    context = StructuredPromptContext(full_prompt="FULL PROMPT", minimal_input="MINIMAL INPUT")

    result = call_with_structured_output(model, DemoSchema, context, "match_agent")

    assert result.degraded is False
    assert result.retry_count == 0
    assert result.error_log == []
    assert result.value is not None
    assert result.value.label == "ok"
    assert model.call_count_ref["count"] == 1
    assert model.bind_calls == []
