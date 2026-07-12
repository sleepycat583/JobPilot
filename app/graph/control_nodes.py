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
    "low_score_gate": "匹配结果已进入低分待审核状态，等待人工确认。",
    "finalize_node": "最终产物占位节点仅用于验证图继续执行，不写入 final_output。",
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


def low_score_gate_node(state: JobAssistantState) -> dict[str, object]:
    """低分确认 Gate。

    只读取 `match_result.low_score_review_required` 来决定是否进入待审核状态。
    为 True 时，将 `review_status` 标记为 `in_review` 并指向 `match_result`；
    为 False 时，仅记录 Gate 已通过，不改写审核生命周期。
    """

    match_result = state.get("match_result")
    low_score_review_required = bool(getattr(match_result, "low_score_review_required", False))
    if low_score_review_required:
        return {
            "current_node": "low_score_gate",
            "review_status": "in_review",
            "review_target": "match_result",
            "execution_history": [_build_event("low_score_gate", "success", "waiting_for_review")],
        }

    return {
        "current_node": "low_score_gate",
        "execution_history": [_build_event("low_score_gate", "success", "gate_passed")],
    }


def finalize_node(_: JobAssistantState) -> dict[str, object]:
    """最终产物占位节点。

    本节点只用于证明低分 Gate 通过后图还能继续执行，不写入 final_output。
    """

    return {
        "current_node": "finalize_node",
        "execution_history": [_build_event("finalize_node", "success", "finalization_placeholder")],
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
