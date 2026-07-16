"""文档 §5.4 rolling summary 节点测试。"""

from dataclasses import dataclass, field
from typing import Any

from app.graph.rolling_summary import (
    RECENT_MESSAGE_COUNT,
    estimate_context_tokens,
    rolling_summary_node,
    should_summarize,
)


@dataclass
class FakeChatModel:
    responses: list[Any]
    invoke_calls: int = 0
    prompts: list[str] = field(default_factory=list)

    def invoke(self, prompt: str) -> Any:
        self.prompts.append(prompt)
        response = self.responses[min(self.invoke_calls, len(self.responses) - 1)]
        self.invoke_calls += 1
        if isinstance(response, Exception):
            raise response
        return response

    def bind(self, **_: Any) -> "FakeChatModel":
        return self


def _messages(count: int, content: str = "内容") -> list[dict[str, str]]:
    return [{"role": "user" if index % 2 == 0 else "assistant", "content": f"{content}{index}"} for index in range(count)]


def _summary_json() -> str:
    return ('{"user_goals":["准备后端岗位"],"confirmed_facts_and_decisions":["使用 Python"],'
            '"corrections_and_constraints":[],"unresolved_questions_and_next_actions":["补充项目经历"],'
            '"approval_feedback":[],"interview_topics_and_scores":["并发：80分"]}')


def test_message_threshold_is_inclusive_and_retains_six_recent_messages() -> None:
    messages = _messages(12)
    model = FakeChatModel([_summary_json()])

    update = rolling_summary_node({"messages": messages, "summarized_message_count": 2}, model)

    assert model.invoke_calls == 1
    assert update["messages"] == messages[-RECENT_MESSAGE_COUNT:]
    assert update["summarized_message_count"] == 8
    assert "用户目标: 准备后端岗位" in str(update["conversation_summary"])


def test_token_threshold_is_strict_or_condition() -> None:
    messages = _messages(7, "中" * 1_150)
    assert len(messages) < 12
    assert estimate_context_tokens("", messages) > 8_000
    assert should_summarize(messages, "") is True

    exactly_threshold = _messages(7, "x" * 1_140)
    # ASCII 输入通过精确补齐，确保测试 `> 8000` 而非 `>= 8000`。
    needed = 8_000 - estimate_context_tokens("", exactly_threshold)
    exactly_threshold[0]["content"] += "中" * needed
    assert estimate_context_tokens("", exactly_threshold) == 8_000
    assert should_summarize(exactly_threshold, "") is False


def test_three_invalid_json_responses_preserve_all_conversation_state() -> None:
    messages = _messages(12)
    model = FakeChatModel(["not json", "still not json", "also not json"])
    state = {"messages": messages, "conversation_summary": "已有摘要", "summarized_message_count": 4}

    update = rolling_summary_node(state, model)

    assert model.invoke_calls == 3
    assert not {"messages", "conversation_summary", "summarized_message_count"} & update.keys()
    assert state == {"messages": messages, "conversation_summary": "已有摘要", "summarized_message_count": 4}
    assert update["error_log"][-1]["code"] == "SUMMARY_FAILED"


def test_model_exception_preserves_all_conversation_state() -> None:
    messages = _messages(12)
    state = {"messages": messages, "conversation_summary": "已有摘要", "summarized_message_count": 4}

    update = rolling_summary_node(state, FakeChatModel([RuntimeError("network down")]))

    assert not {"messages", "conversation_summary", "summarized_message_count"} & update.keys()
    assert state == {"messages": messages, "conversation_summary": "已有摘要", "summarized_message_count": 4}
    assert update["error_log"][-1]["code"] == "SUMMARY_FAILED"


def test_second_summary_uses_old_summary_and_only_new_old_messages() -> None:
    messages = _messages(12)
    model = FakeChatModel([_summary_json()])

    rolling_summary_node({"messages": messages, "conversation_summary": "早期事实"}, model)

    assert "Old summary:\n早期事实" in model.prompts[0]
    assert str(messages[-RECENT_MESSAGE_COUNT]) not in model.prompts[0]