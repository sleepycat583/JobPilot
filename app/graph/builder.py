"""Graph 拓扑构建。

本文件组装 Supervisor、顺序任务队列、业务节点和低分 Gate 等确定性控制节点。
Worker 只产出业务结果；队列消费和下一跳选择始终由 Graph 控制节点负责。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.graph import END, StateGraph
from langgraph.checkpoint.base import BaseCheckpointSaver

from app.agents.jd_parser import jd_parser_node
from app.agents.jd_parser import CONTENT_INSUFFICIENT_CODE, EXTRACTION_UNAVAILABLE_CODE
from app.agents.resume_matcher import resume_matcher_node
from app.agents.interview_simulator import (
    ask_question_node,
    evaluate_answer_node,
    generate_review_report_node,
    interview_decision_node,
    interview_plan_node,
)
from app.agents.supervisor import supervisor_node
from app.graph.control_nodes import (
    clarify_node,
    error_node,
    final_review_gate_node,
    finalize_node,
    interview_await_answer_node,
    low_score_gate_node,
    low_score_cancelled_node,
    out_of_scope_node,
    prepare_low_score_review_node,
    prepare_final_review_node,
    revision_dispatch_node,
)
from app.graph.routing import resolve_route_node
from app.rag.chroma_store import ChromaResumeStore
from app.schemas.state import ExecutionEvent, JobAssistantState
from app.tools.company_search import SearchBackend


def build_graph(
    chat_model: BaseChatModel,
    search_backend: SearchBackend | None = None,
    resume_store: ChromaResumeStore | Any | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
):
    """构建并编译按 task_queue 顺序执行的图拓扑。

    参数：
        chat_model: 供 Supervisor / JD 解析 / 简历匹配复用的聊天模型。
        search_backend: JD 解析可选搜索工具适配器。
        resume_store: 简历 Chroma 检索适配器；缺失时仅启用测试占位匹配节点。
    """

    graph = StateGraph(JobAssistantState)

    def supervisor_with_model(state: JobAssistantState) -> dict[str, object]:
        return supervisor_node(state, chat_model)

    def jd_parser_with_dependencies(state: JobAssistantState) -> dict[str, object]:
        return {
            **jd_parser_node(state, chat_model, search_backend),
            "review_status": "pending",
            "review_target": "jd_parsed",
            "review_feedback": None,
        }

    def resume_matcher_with_dependencies(state: JobAssistantState) -> dict[str, object]:
        if resume_store is None:
            update = resume_matcher_placeholder(state)
        else:
            update = resume_matcher_node(state, chat_model, resume_store)
        if update.get("match_result") is None:
            return update
        return {**update, "review_status": "pending", "review_target": "match_result", "review_feedback": None}

    graph.add_node("supervisor", supervisor_with_model)
    graph.add_node("queue_dispatch", queue_dispatch_node)
    graph.add_node("jd_parser", jd_parser_with_dependencies)
    graph.add_node("resume_matcher", resume_matcher_with_dependencies)
    graph.add_node("low_score_gate", low_score_gate_node)
    graph.add_node("prepare_low_score_review", prepare_low_score_review_node)
    graph.add_node("low_score_cancelled", low_score_cancelled_node)
    graph.add_node("prepare_final_review", prepare_final_review_node)
    graph.add_node("final_review_gate", final_review_gate_node)
    graph.add_node("revision_dispatch", revision_dispatch_node)
    graph.add_node("finalize_node", finalize_node)
    def interview_plan_with_model(state: JobAssistantState) -> dict[str, object]:
        return interview_plan_node(state, chat_model)

    def ask_question_with_model(state: JobAssistantState) -> dict[str, object]:
        return ask_question_node(state, chat_model)

    def evaluate_answer_with_model(state: JobAssistantState) -> dict[str, object]:
        return evaluate_answer_node(state, chat_model)

    def generate_report_with_model(state: JobAssistantState) -> dict[str, object]:
        return generate_review_report_node(state, chat_model)

    graph.add_node("interview_simulator", interview_plan_with_model)
    graph.add_node("ask_question", ask_question_with_model)
    graph.add_node("interview_await_answer", interview_await_answer_node)
    graph.add_node("evaluate_answer", evaluate_answer_with_model)
    graph.add_node("interview_decision", interview_decision_node)
    graph.add_node("generate_review_report", generate_report_with_model)
    graph.add_node("clarify_node", clarify_node)
    graph.add_node("out_of_scope_node", out_of_scope_node)
    graph.add_node("error_node", error_node)

    graph.set_entry_point("supervisor")

    graph.add_conditional_edges(
        "supervisor",
        resolve_route_node,
        {
            "jd_parser": "queue_dispatch",
            "resume_matcher": "queue_dispatch",
            "interview_simulator": "queue_dispatch",
            "clarify_node": "clarify_node",
            "out_of_scope_node": "out_of_scope_node",
            "error_node": "error_node",
        },
    )

    graph.add_conditional_edges(
        "queue_dispatch",
        _resolve_queue_dispatch_route,
        {
            "jd_parser": "jd_parser",
            "resume_matcher": "resume_matcher",
            "interview_simulator": "interview_simulator",
            "finalize": "prepare_final_review",
            "error": "error_node",
        },
    )
    graph.add_conditional_edges(
        "jd_parser",
        _resolve_jd_completion_route,
        {"prepare_review": "prepare_final_review", "error": "error_node"},
    )
    graph.add_edge("prepare_final_review", "final_review_gate")
    graph.add_conditional_edges(
        "final_review_gate",
        _resolve_final_review_route,
        {"approve": "finalize_node", "reject": "revision_dispatch"},
    )
    graph.add_conditional_edges(
        "revision_dispatch",
        _resolve_revision_target_route,
        {
            "queue_dispatch": "queue_dispatch",
            "jd_parser": "jd_parser",
            "resume_matcher": "resume_matcher",
            "generate_review_report": "generate_review_report",
            "error": "error_node",
        },
    )
    graph.add_conditional_edges(
        "resume_matcher",
        _resolve_match_result_route,
        {"low_score": "prepare_low_score_review", "prepare_review": "prepare_final_review"},
    )
    graph.add_edge("prepare_low_score_review", "low_score_gate")
    graph.add_conditional_edges(
        "low_score_gate",
        _resolve_low_score_gate_route,
        {
            "continue": "prepare_final_review",
            "cancel": "low_score_cancelled",
            "revise": "revision_dispatch",
        },
    )
    graph.add_conditional_edges(
        "finalize_node",
        _resolve_finalize_route,
        {"continue": "queue_dispatch", "end": END},
    )
    graph.add_edge("low_score_cancelled", END)
    graph.add_edge("interview_simulator", "ask_question")
    graph.add_edge("ask_question", "interview_await_answer")
    graph.add_conditional_edges(
        "interview_await_answer",
        _resolve_interview_resume_route,
        {"wait": "interview_await_answer", "evaluate": "evaluate_answer", "report": "generate_review_report"},
    )
    graph.add_edge("evaluate_answer", "interview_decision")
    graph.add_conditional_edges(
        "interview_decision",
        _resolve_interview_decision_route,
        {"ask": "ask_question", "report": "generate_review_report"},
    )
    graph.add_edge("generate_review_report", "prepare_final_review")
    graph.add_edge("clarify_node", END)
    graph.add_edge("out_of_scope_node", END)
    graph.add_edge("error_node", END)

    return graph.compile(checkpointer=checkpointer)


def resume_matcher_placeholder(_: JobAssistantState) -> dict[str, object]:
    """缺少 resume_store 时的简历匹配占位节点。"""

    return {
        "current_node": "resume_matcher",
        "execution_history": [_build_event("resume_matcher", "success", "week1_placeholder")],
        "match_result": {
            "total_score": 100.0,
            "dimension_scores": {},
            "matched_items": [],
            "strengths": [],
            "gaps": [],
            "recommendations": [],
            "low_score_review_required": False,
            "resume_version": "placeholder",
        },
    }


def queue_dispatch_node(state: JobAssistantState) -> dict[str, object]:
    """消费一个待执行任务并将剩余队列持久化到 State。

    参数：
        state: 包含 Supervisor 初始化的有序 `task_queue` 的全局状态。

    返回：
        覆盖更新后的剩余队列和调度执行轨迹；具体 Worker 由条件边选择。
    """

    task_queue = list(state.get("task_queue", []))
    if not task_queue:
        return {"current_node": "queue_dispatch"}
    task = task_queue[0]
    return {
        "task_queue": task_queue[1:],
        "current_node": task,
        "execution_history": [_build_event("queue_dispatch", "success", f"dispatch:{task}")],
    }


def _resolve_queue_dispatch_route(state: JobAssistantState) -> str:
    """根据队首任务确定下一 Worker，空队列进入最终节点。"""

    current_node = state.get("current_node")
    if current_node == "queue_dispatch":
        return "finalize"
    return {"jd_parse": "jd_parser", "resume_match": "resume_matcher", "mock_interview": "interview_simulator"}.get(current_node, "error")


def _resolve_jd_completion_route(state: JobAssistantState) -> str:
    """阻断降级 JD；成功产物必须先通过最终核可才可消费下一项。"""

    error_codes = {entry.get("code") for entry in state.get("error_log", [])}
    if {CONTENT_INSUFFICIENT_CODE, EXTRACTION_UNAVAILABLE_CODE} & error_codes:
        return "error"
    return "prepare_review"


def _resolve_low_score_gate_route(state: JobAssistantState) -> str:
    """根据低分标志决定 Gate 后续路径。"""

    if state.get("review_status") != "rejected":
        return "continue"
    return "revise" if state.get("task_queue") else "cancel"


def _resolve_match_result_route(state: JobAssistantState) -> str:
    """低分先进入确认 Gate，其余匹配结果直接等待最终核可。"""

    match_result = state.get("match_result")
    return "low_score" if bool(getattr(match_result, "low_score_review_required", False)) else "prepare_review"


def _resolve_final_review_route(state: JobAssistantState) -> str:
    """根据最终核可命令决定格式化产物或进入修订分发。"""

    return "reject" if state.get("review_status") == "rejected" else "approve"


def _resolve_finalize_route(state: JobAssistantState) -> str:
    """已批准当前产物后，仅在仍有任务时回到队列分发。"""

    return "continue" if state.get("task_queue") else "end"


def _resolve_revision_target_route(state: JobAssistantState) -> str:
    """按审核目标将修订流返回对应 Worker，避免重新执行无关任务。"""

    # 面试复盘修订只允许重建报告；不能因未来队列策略变动而重新消费业务任务。
    if state.get("review_target") == "interview_report":
        return "generate_review_report"
    return {"jd_parsed": "jd_parser", "match_result": "resume_matcher"}.get(state.get("review_target"), "error")


def _resolve_interview_resume_route(state: JobAssistantState) -> str:
    """HITL 恢复后只路由当前题评价或报告，不进入最终核可。"""

    interview_state = state.get("interview_state")
    status = getattr(interview_state, "status", None)
    if status == "waiting":
        return "wait"
    return "evaluate" if status == "evaluating" else "report"


def _resolve_interview_decision_route(state: JobAssistantState) -> str:
    """由确定性 decision action 决定继续出题或生成 report。"""

    return "report" if state.get("interview_next_action") == "finish" else "ask"


def _build_event(node: str, event: str, detail: str) -> ExecutionEvent:
    return {
        "node": node,
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "detail": detail,
    }
