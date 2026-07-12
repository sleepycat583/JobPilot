"""确定性控制节点。

这些节点不调用聊天模型，不承担 Agent 业务推理，只负责确定性控制流处理。
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.schemas.state import ErrorEntry, ExecutionEvent, JobAssistantState

CONTROL_MESSAGES = {
    "clarify_node": "请补充你希望执行的求职任务，例如分析 JD、匹配简历或开始模拟面试。",
    "out_of_scope_node": "当前请求不属于求职辅助系统支持的范围，请改为 JD 解析、简历匹配或模拟面试相关请求。",
    "interview_simulator": "模拟面试节点将在 Week2 实现，本阶段仅保留路由占位。",
}


def get_control_message(node_name: str) -> str:
    """返回控制节点对应的确定性提示文案。"""

    return CONTROL_MESSAGES[node_name]


def clarify_node(_: JobAssistantState) -> dict[str, object]:
    """确定性澄清节点，不调用 LLM。"""

    return {
        "current_node": "clarify_node",
        "execution_history": [_build_event("clarify_node", "success", "clarification_required")],
    }


def out_of_scope_node(_: JobAssistantState) -> dict[str, object]:
    """确定性超范围节点，不调用 LLM。"""

    return {
        "current_node": "out_of_scope_node",
        "execution_history": [_build_event("out_of_scope_node", "success", "request_out_of_scope")],
    }


def interview_simulator(_: JobAssistantState) -> dict[str, object]:
    """Week1 的面试占位节点，不实现面试逻辑。"""

    return {
        "current_node": "interview_simulator",
        "execution_history": [_build_event("interview_simulator", "success", "week2_placeholder")],
    }


def error_node(state: JobAssistantState) -> dict[str, object]:
    """确定性错误处理节点。"""

    if state.get("error_log"):
        return {
            "current_node": "error_node",
            "execution_history": [_build_event("error_node", "error", "deterministic_error_handled")],
        }

    return {
        "current_node": "error_node",
        "execution_history": [_build_event("error_node", "error", "routing_error")],
        "error_log": [_build_error_entry("ROUTING_ERROR", "error_node", False, 0, "No valid route decision available")],
    }


def _build_event(node: str, event: str, detail: str) -> ExecutionEvent:
    return {
        "node": node,
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "detail": detail,
    }


def _build_error_entry(code: str, node: str, retryable: bool, attempt: int, message: str) -> ErrorEntry:
    return {
        "code": code,
        "node": node,
        "message": message,
        "retryable": retryable,
        "attempt": attempt,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "raw_output_excerpt": None,
    }
