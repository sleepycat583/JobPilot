"""Graph 最小拓扑构建。

本文件只负责搭建 Week1 所需的最小 StateGraph 骨架与路由连通性，
不实现 JD 解析、简历匹配或模拟面试的真实业务逻辑。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.graph import END, StateGraph

from app.agents.jd_parser import jd_parser_node
from app.agents.supervisor import supervisor_node
from app.graph.control_nodes import clarify_node, error_node, interview_simulator, out_of_scope_node
from app.graph.routing import resolve_route_node
from app.schemas.state import ExecutionEvent, JobAssistantState
from app.tools.company_search import SearchBackend


def build_graph(chat_model: BaseChatModel, search_backend: SearchBackend | None = None):
    """构建并编译 Week1 最小图拓扑。"""

    graph = StateGraph(JobAssistantState)

    def supervisor_with_model(state: JobAssistantState) -> dict[str, object]:
        return supervisor_node(state, chat_model)

    def jd_parser_with_dependencies(state: JobAssistantState) -> dict[str, object]:
        return jd_parser_node(state, chat_model, search_backend)

    graph.add_node("supervisor", supervisor_with_model)
    graph.add_node("jd_parser", jd_parser_with_dependencies)
    graph.add_node("resume_matcher", resume_matcher_placeholder)
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
    graph.add_edge("resume_matcher", END)
    graph.add_edge("interview_simulator", END)
    graph.add_edge("clarify_node", END)
    graph.add_edge("out_of_scope_node", END)
    graph.add_edge("error_node", END)

    return graph.compile()


def resume_matcher_placeholder(_: JobAssistantState) -> dict[str, object]:
    """Week1 的简历匹配占位节点。"""

    return {
        "current_node": "resume_matcher",
        "execution_history": [_build_event("resume_matcher", "success", "week1_placeholder")],
    }


def _build_event(node: str, event: str, detail: str) -> ExecutionEvent:
    return {
        "node": node,
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "detail": detail,
    }
