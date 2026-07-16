"""文档 §5.4 的对话滚动摘要节点。

由 Graph 在 Supervisor 前调用；依赖聊天模型及通用结构化输出重试服务，不承担路由职责。
"""

from __future__ import annotations

from datetime import datetime, timezone
from math import ceil
from typing import Any

from app.schemas.conversation import ConversationSummary
from app.schemas.state import ErrorEntry, JobAssistantState
from app.services.structured_output import StructuredPromptContext, call_with_structured_output

MESSAGE_THRESHOLD = 12
TOKEN_THRESHOLD = 8_000
RECENT_MESSAGE_COUNT = 6
MESSAGE_OVERHEAD_TOKENS = 4


def rolling_summary_node(state: JobAssistantState, chat_model: Any) -> dict[str, object]:
    """按阈值压缩旧消息，成功时原子替换，失败时不修改对话状态。

    参数：state 为当前全局状态；chat_model 为注入的聊天模型。
    返回：成功时含摘要、最近消息与累计计数；无需摘要或失败时不写这些字段。
    """
    messages = _valid_messages(state.get("messages", []))
    old_summary = state.get("conversation_summary", "")
    if not should_summarize(messages, old_summary):
        return {"current_node": "rolling_summary"}

    compressible_messages = messages[:-RECENT_MESSAGE_COUNT]
    context = StructuredPromptContext(
        full_prompt=_build_summary_prompt(old_summary, compressible_messages),
        minimal_input=_build_summary_prompt("", compressible_messages),
    )
    try:
        result = call_with_structured_output(chat_model, ConversationSummary, context, "rolling_summary")
    except Exception as exc:  # 模型传输异常不属于通用 JSON 重试范围，也必须无损降级。
        return _failed_update([], str(exc))

    if result.value is None:
        return _failed_update(result.error_log, "Summary model failed after structured-output retries")

    rendered = render_summary(result.value)
    if not rendered:
        return _failed_update(result.error_log, "Summary model returned no retainable facts")
    return {
        "conversation_summary": rendered,
        "messages": messages[-RECENT_MESSAGE_COUNT:],
        "summarized_message_count": state.get("summarized_message_count", 0) + len(compressible_messages),
        "current_node": "rolling_summary",
        "error_log": result.error_log,
    }


def should_summarize(messages: list[dict[str, str]], summary: str) -> bool:
    """根据 §5.4 的消息数或估算 token 任一阈值判断是否压缩。"""
    return len(messages) > RECENT_MESSAGE_COUNT and (
        len(messages) >= MESSAGE_THRESHOLD or estimate_context_tokens(summary, messages) > TOKEN_THRESHOLD
    )


def estimate_context_tokens(summary: str, messages: list[dict[str, str]]) -> int:
    """保守估算中英文混合上下文 token：非 ASCII 按 1、ASCII 每 4 字符按 1。"""
    text = summary + "".join(f"{item['role']}{item['content']}" for item in messages)
    ascii_count = sum(char.isascii() for char in text)
    return (len(text) - ascii_count) + ceil(ascii_count / 4) + len(messages) * MESSAGE_OVERHEAD_TOKENS


def render_summary(summary: ConversationSummary) -> str:
    """将经 Schema 验证的分类事实渲染为稳定的 Prompt 文本。"""
    sections = (
        ("用户目标", summary.user_goals),
        ("已确认事实与决策", summary.confirmed_facts_and_decisions),
        ("纠正与约束", summary.corrections_and_constraints),
        ("未解决问题与后续行动", summary.unresolved_questions_and_next_actions),
        ("人工审批反馈", summary.approval_feedback),
        ("面试主题与关键得分", summary.interview_topics_and_scores),
    )
    return "\n".join(f"{title}: " + "; ".join(values) for title, values in sections if values)


def _valid_messages(value: object) -> list[dict[str, str]]:
    """过滤不符合既有 dict 消息约定的项，避免坏历史阻断主业务。"""
    if not isinstance(value, list):
        return []
    return [
        {"role": item["role"], "content": item["content"]}
        for item in value
        if isinstance(item, dict) and isinstance(item.get("role"), str) and isinstance(item.get("content"), str)
    ]


def _build_summary_prompt(old_summary: str, messages: list[dict[str, str]]) -> str:
    return (
        "Return only JSON matching ConversationSummary. Compress the old conversation below. "
        "Preserve goals, confirmed facts, corrections, unresolved questions, approval feedback, and interview scores. "
        "Do not copy complete resume chunks, complete JD text, or structured Worker outputs.\n"
        f"Old summary:\n{old_summary}\nMessages to compress:\n{messages}"
    )


def _failed_update(error_log: list[ErrorEntry], message: str) -> dict[str, object]:
    """记录 SUMMARY_FAILED；故意不返回三项对话字段以保证无损。"""
    return {
        "current_node": "rolling_summary",
        "error_log": [*error_log, {
            "code": "SUMMARY_FAILED", "node": "rolling_summary", "message": message,
            "retryable": False, "attempt": 2, "timestamp": datetime.now(timezone.utc).isoformat(),
            "raw_output_excerpt": None,
        }],
    }