"""Graph 最小拓扑构建。

本文件负责组装 Week1 的最小 StateGraph：Supervisor 路由、业务节点、
以及低分 Gate 等确定性控制节点。真实业务逻辑仍由各自模块实现。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.graph import END, StateGraph

from app.agents.jd_parser import jd_parser_node
from app.agents.resume_matcher import resume_matcher_node
from app.agents.supervisor import supervisor_node
from app.graph.control_nodes import (
    clarify_node,
    error_node,
    finalize_node,
    interview_simulator,
    low_score_gate_node,
    out_of_scope_node,
)
from app.graph.routing import resolve_route_node
from app.rag.chroma_store import ChromaResumeStore
from app.schemas.state import ExecutionEvent, JobAssistantState
from app.tools.company_search import SearchBackend


def build_graph(
    chat_model: BaseChatModel,
    search_backend: SearchBackend | None = None,
    resume_store: ChromaResumeStore | Any | None = None,
):
    """构建并编译 Week1 最小图拓扑。

    参数：
        chat_model: 供 Supervisor / JD 解析 / 简历匹配复用的聊天模型。
        search_backend: JD 解析可选搜索工具适配器。
        resume_store: 简历 Chroma 检索适配器；缺失时仅启用测试占位匹配节点。
    """

    graph = StateGraph(JobAssistantState)

    def supervisor_with_model(state: JobAssistantState) -> dict[str, object]:
        return supervisor_node(state, chat_model)

    def jd_parser_with_dependencies(state: JobAssistantState) -> dict[str, object]:
        return jd_parser_node(state, chat_model, search_backend)

    def resume_matcher_with_dependencies(state: JobAssistantState) -> dict[str, object]:
        if resume_store is None:
            return resume_matcher_placeholder(state)
        return resume_matcher_node(state, chat_model, resume_store)

    graph.add_node("supervisor", supervisor_with_model)
    graph.add_node("jd_parser", jd_parser_with_dependencies)
    graph.add_node("resume_matcher", resume_matcher_with_dependencies)
    graph.add_node("low_score_gate", low_score_gate_node)
    graph.add_node("finalize_node", finalize_node)
    graph.add_node("interview_simulator", interview_simulator)
    graph.add_node("clarify_node", clarify_node)
    graph.add_node("out_of_scope_node", out_of_scope_node)
    graph.add_node("error_node", error_node)

    graph.set_entry_point("supervisor")

    graph.add_conditional_edges(
        "supervisor",
        resolve_route_node,
        {
            "jd_parser": "jd_parser",
            "resume_matcher": "resume_matcher",
            "interview_simulator": "interview_simulator",
            "clarify_node": "clarify_node",
            "out_of_scope_node": "out_of_scope_node",
            "error_node": "error_node",
        },
    )

    graph.add_edge("jd_parser", END)
    graph.add_edge("resume_matcher", "low_score_gate")
    graph.add_conditional_edges(
        "low_score_gate",
        _resolve_low_score_gate_route,
        {
            "await_review": END,
            "continue": "finalize_node",
        },
    )
    graph.add_edge("finalize_node", END)
    graph.add_edge("interview_simulator", END)
    graph.add_edge("clarify_node", END)
    graph.add_edge("out_of_scope_node", END)
    graph.add_edge("error_node", END)

    return graph.compile()


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


def _resolve_low_score_gate_route(state: JobAssistantState) -> str:
    """根据低分标志决定 Gate 后续路径。"""

    match_result = state.get("match_result")
    if bool(getattr(match_result, "low_score_review_required", False)):
        return "await_review"
    return "continue"


def _build_event(node: str, event: str, detail: str) -> ExecutionEvent:
    return {
        "node": node,
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "detail": detail,
    }
