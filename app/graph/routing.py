"""第⑥步路由函数与冻结映射表。

本文件只提供 conditional edge 所需的读取与解析函数，不在本步实际调用 add_conditional_edges。
真正的 Graph 构图留到第⑦步完成。
"""

from __future__ import annotations

from app.schemas.state import JobAssistantState

ROUTE_NODE_MAP = {
    "jd_parse": "jd_parser",
    "resume_match": "resume_matcher",
    "mock_interview": "interview_simulator",
    "clarify": "clarify_node",
    "out_of_scope": "out_of_scope_node",
    "error": "error_node",
}


def route_key(state: JobAssistantState) -> str:
    """提取路由键。

    只读取 `state["route_decision"].route`，不做关键词匹配或其他启发式判断。
    当 route_decision 缺失时，返回内部控制键 `error`。
    """

    decision = state.get("route_decision")
    if decision is None:
        return "error"
    return decision.route


def resolve_route_node(state: JobAssistantState) -> str:
    """将路由键映射为节点名。

    未知键或缺失决策统一导向 `error_node`，不允许猜测进入任何 Worker。
    """

    return ROUTE_NODE_MAP.get(route_key(state), "error_node")
