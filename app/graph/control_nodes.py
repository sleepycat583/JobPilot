"""确定性控制节点。

这些节点不调用聊天模型，不承担 Agent 业务推理，只负责确定性控制流处理。
"""

from __future__ import annotations

from datetime import datetime, timezone

from langgraph.types import interrupt

from app.schemas.review import FinalReviewInterruptPayload, LowScoreInterruptPayload
from app.schemas.state import ErrorEntry, ExecutionEvent, JobAssistantState

CONTROL_MESSAGES = {
    "clarify_node": "请补充你希望执行的求职任务，例如分析 JD、匹配简历或开始模拟面试。",
    "out_of_scope_node": "当前请求不属于求职辅助系统支持的范围，请改为 JD 解析、简历匹配或模拟面试相关请求。",
    "interview_simulator": "模拟面试节点将在 Week2 实现，本阶段仅保留路由占位。",
    "low_score_gate": "匹配结果已进入低分待审核状态，等待人工确认。",
    "low_score_cancelled": "用户已取消低分匹配任务。",
    "final_review_gate": "候选产物等待最终人工核可。",
    "finalize_node": "最终产物已通过人工核可。",
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


def prepare_low_score_review_node(_: JobAssistantState) -> dict[str, object]:
    """在 interrupt 前持久化低分审核状态，避免恢复时重放丢失状态。"""

    return {
        "current_node": "prepare_low_score_review",
        "review_status": "in_review",
        "review_target": "match_result",
        "execution_history": [_build_event("prepare_low_score_review", "interrupt", "low_score_review_required")],
    }


def prepare_final_review_node(state: JobAssistantState) -> dict[str, object]:
    """在最终核可 interrupt 前持久化候选产物的审核状态。"""

    target = state.get("review_target")
    if target not in {"jd_parsed", "match_result", "interview_report"}:
        raise ValueError("Final review requires a valid review_target")
    return {
        "current_node": "prepare_final_review",
        "review_status": "in_review",
        "execution_history": [_build_event("prepare_final_review", "interrupt", f"final_review:{target}")],
    }


def final_review_gate_node(state: JobAssistantState) -> dict[str, object]:
    """等待人工核可当前候选产物，并记录 approve 或 reject 决策。"""

    target = state.get("review_target")
    if state.get("review_status") != "in_review" or target is None:
        raise ValueError("Final review gate requires an in-review target")
    payload = FinalReviewInterruptPayload(
        type="final_review",
        target=target,
        draft=_build_final_review_draft(state, target),
        accepted_actions=["approve", "reject"],
    )
    decision = interrupt(payload.model_dump(mode="json"))
    action = decision.get("action") if isinstance(decision, dict) else None
    if action == "approve":
        return {
            "current_node": "final_review_gate",
            "review_status": "approved",
            "execution_history": [_build_event("final_review_gate", "resume", "final_review_approved")],
        }
    if action == "reject":
        return {
            "current_node": "final_review_gate",
            "review_status": "rejected",
            "review_feedback": str(decision.get("feedback", "")),
            "execution_history": [_build_event("final_review_gate", "resume", "final_review_rejected")],
        }
    raise ValueError("Unsupported final review action")


def revision_dispatch_node(state: JobAssistantState) -> dict[str, object]:
    """在被拒绝的审核决策已持久化后，将目标转入 revising 状态。"""

    if state.get("review_status") != "rejected":
        raise ValueError("Revision dispatch requires a rejected review")
    return {
        "current_node": "revision_dispatch",
        "review_status": "revising",
        "execution_history": [_build_event("revision_dispatch", "success", f"revising:{state.get('review_target')}")],
    }


def low_score_gate_node(state: JobAssistantState) -> dict[str, object]:
    """低分确认 Gate。

    审核前状态由 prepare 节点先持久化；本节点只在低分审核中断后消费
    continue/cancel 命令，确保恢复时不会依赖进程内变量。
    """

    match_result = state.get("match_result")
    low_score_review_required = bool(getattr(match_result, "low_score_review_required", False))
    if low_score_review_required and state.get("review_status") == "in_review":
        payload = LowScoreInterruptPayload(
            type="low_match_score",
            score=match_result.total_score,
            threshold=60.0,
            top_gaps=match_result.gaps[:5],
            accepted_actions=["continue", "cancel"],
        )
        decision = interrupt(payload.model_dump())
        action = decision.get("action") if isinstance(decision, dict) else None
        if action == "continue":
            return {
                "current_node": "low_score_gate",
                "review_status": "approved",
                "execution_history": [_build_event("low_score_gate", "resume", "low_score_continue")],
            }
        if action == "cancel":
            return {
                "current_node": "low_score_gate",
                "review_status": "rejected",
                "review_feedback": str(decision.get("feedback", "")),
                "execution_history": [_build_event("low_score_gate", "resume", "low_score_cancel")],
            }
        raise ValueError("Unsupported low score review action")

    return {
        "current_node": "low_score_gate",
        "execution_history": [_build_event("low_score_gate", "success", "gate_passed")],
    }


def finalize_node(state: JobAssistantState) -> dict[str, object]:
    """在最终核可后格式化当前候选产物，不调用 LLM 改写内容。"""

    target = state.get("review_target")
    if state.get("review_status") != "approved" or target is None:
        raise ValueError("Finalize requires an approved review target")
    return {
        "current_node": "finalize_node",
        "final_output": {
            "type": target,
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "content": _build_final_review_draft(state, target),
        },
        "execution_history": [_build_event("finalize_node", "success", f"finalized:{target}")],
    }


def low_score_cancelled_node(_: JobAssistantState) -> dict[str, object]:
    """结束被用户取消的低分匹配，不生成最终产物。"""

    return {
        "current_node": "low_score_cancelled",
        "execution_history": [_build_event("low_score_cancelled", "success", "task_cancelled")],
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


def _build_final_review_draft(state: JobAssistantState, target: str) -> dict[str, object]:
    """提取最终核可所需的结构化草稿，避免在控制节点生成新业务内容。"""

    if target == "jd_parsed":
        value = state.get("jd_parsed")
    elif target == "match_result":
        value = state.get("match_result")
    elif target == "interview_report":
        interview_state = state.get("interview_state")
        value = getattr(interview_state, "report", None)
    else:
        raise ValueError(f"Unsupported final review target: {target}")
    if value is None:
        raise ValueError(f"Final review target has no draft: {target}")
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value)


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
