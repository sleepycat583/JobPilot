"""Supervisor 节点。

本文件只实现文档 §3.1 / §6.1 中的路由职责：
- 调用结构化输出服务获取 RouterDecision
- 处理空输入、超长输入、低置信度澄清等确定性规则
- 返回 LangGraph State update

它不调用任何 Worker，不执行 JD 解析、简历匹配或面试逻辑。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from app.constants import MAX_INPUT_LENGTH
from app.schemas.router import RouterDecision
from app.schemas.state import ErrorEntry, ExecutionEvent, JobAssistantState
from app.services.structured_output import StructuredPromptContext, call_with_structured_output


def supervisor_node(state: JobAssistantState, chat_model: BaseChatModel) -> dict[str, object]:
    """执行 Supervisor 路由决策。

    参数：
        state: 当前 LangGraph 全局状态。
        chat_model: 已构建好的聊天模型实例，由外部注入。

    返回：
        仅包含 State update 的字典，不原地修改传入 state。
    """

    user_input = state.get("user_input", "")
    stripped_input = user_input.strip()

    if not stripped_input:
        decision = RouterDecision(
            route="clarify",
            confidence=1.0,
            reason="User input is empty",
            task_queue=[],
        )
        return {
            "route_decision": decision,
            "task_queue": [],
            "current_node": "supervisor",
            "retry_count": {"supervisor": 0},
            "error_log": [_build_error_entry("INPUT_EMPTY", "supervisor", False, 0, "User input is empty")],
        }

    if len(user_input) > MAX_INPUT_LENGTH:
        return {
            "route_decision": None,
            "task_queue": [],
            "current_node": "supervisor",
            "retry_count": {"supervisor": 0},
            "error_log": [
                _build_error_entry(
                    "INPUT_TOO_LONG",
                    "supervisor",
                    False,
                    0,
                    f"Input length {len(user_input)} exceeds maximum {MAX_INPUT_LENGTH}",
                )
            ],
        }

    jd_recovery_required = _has_jd_extraction_failure(state)
    prompt_context = StructuredPromptContext(
        full_prompt=_build_supervisor_prompt(stripped_input, state),
        minimal_input=stripped_input,
    )
    result = call_with_structured_output(chat_model, RouterDecision, prompt_context, "supervisor")

    if result.value is None:
        return {
            "route_decision": None,
            "task_queue": [],
            "current_node": "supervisor",
            "retry_count": {"supervisor": result.retry_count},
            "error_log": result.error_log,
        }

    decision = result.value
    if jd_recovery_required:
        # JD Worker 已提供最小降级对象；此处只允许 Supervisor 要求补充或结束，避免重入解析循环。
        if decision.route not in {"clarify", "out_of_scope"}:
            decision = decision.model_copy(update={"route": "clarify", "task_queue": []})
    elif decision.confidence < 0.70:
        decision = RouterDecision(
            route="clarify",
            confidence=decision.confidence,
            reason=decision.reason,
            task_queue=[],
        )
    elif decision.route in {"jd_parse", "resume_match", "mock_interview"} and not decision.task_queue:
        decision = decision.model_copy(update={"task_queue": [decision.route]})

    return {
        "route_decision": decision,
        "task_queue": decision.task_queue,
        "current_node": "supervisor",
        "retry_count": {"supervisor": result.retry_count},
        "error_log": result.error_log,
    }


def _build_supervisor_prompt(user_input: str, state: JobAssistantState) -> str:
    """构造 Supervisor 的完整业务 Prompt。"""

    recent_messages = _recent_messages_for_prompt(state.get("messages", []), user_input)

    return (
        "You are a routing supervisor. Return only a JSON object that matches RouterDecision.\n"
        "Allowed routes: jd_parse, resume_match, mock_interview, clarify, out_of_scope.\n"
        "Do not perform JD parsing, resume matching, or interview generation.\n"
        f"User input: {user_input}\n"
        f"Conversation summary: {state.get('conversation_summary', '')}\n"
        f"Recent conversation messages: {recent_messages}\n"
        f"Has JD: {state.get('jd_parsed') is not None}\n"
        f"Has match: {state.get('match_result') is not None}\n"
        f"Interview active: {state.get('interview_state') is not None}\n"
        f"JD extraction recovery required: {_has_jd_extraction_failure(state)}\n"
        "If JD extraction recovery is required, choose only clarify (ask user to paste a complete JD) "
        "or out_of_scope (end the request); do not dispatch a Worker."
    )


def _has_jd_extraction_failure(state: JobAssistantState) -> bool:
    """判断当前是否为 JD Worker 降级后的 Supervisor 恢复调用。"""
    return any(
        entry.get("node") == "jd_parser" and entry.get("code") == "JD_EXTRACTION_UNAVAILABLE"
        for entry in state.get("error_log", [])
    )


def _recent_messages_for_prompt(value: object, user_input: str) -> list[dict[str, str]]:
    """返回合法最近消息，并避免把当前用户输入在 Prompt 中重复展示。"""
    if not isinstance(value, list):
        return []
    messages = [
        {"role": item["role"], "content": item["content"]}
        for item in value
        if isinstance(item, dict) and isinstance(item.get("role"), str) and isinstance(item.get("content"), str)
    ]
    if messages and messages[-1]["role"] == "user" and messages[-1]["content"].strip() == user_input:
        return messages[:-1]
    return messages


def _build_error_entry(code: str, node: str, retryable: bool, attempt: int, message: str) -> ErrorEntry:
    """构造符合 ErrorEntry 契约的错误记录。"""

    return {
        "code": code,
        "node": node,
        "message": message,
        "retryable": retryable,
        "attempt": attempt,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "raw_output_excerpt": None,
    }
