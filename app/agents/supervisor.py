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
    if decision.confidence < 0.70:
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

    return (
        "You are a routing supervisor. Return only a JSON object that matches RouterDecision.\n"
        "Allowed routes: jd_parse, resume_match, mock_interview, clarify, out_of_scope.\n"
        "Do not perform JD parsing, resume matching, or interview generation.\n"
        f"User input: {user_input}\n"
        f"Conversation summary: {state.get('conversation_summary', '')}\n"
        f"Has JD: {state.get('jd_parsed') is not None}\n"
        f"Has match: {state.get('match_result') is not None}\n"
        f"Interview active: {state.get('interview_state') is not None}"
    )


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
