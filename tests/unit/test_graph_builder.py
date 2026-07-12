"""Graph 最小拓扑测试。"""

from dataclasses import dataclass, field
from typing import Any

import pytest
from langgraph.graph import END

from app.constants import MAX_INPUT_LENGTH
from app.graph.builder import build_graph


@dataclass
class FakeChatModel:
    result: Any
    invoke_calls: int = 0
    prompts: list[str] = field(default_factory=list)

    def invoke(self, prompt: str) -> Any:
        self.invoke_calls += 1
        self.prompts.append(prompt)
        return self.result

    def bind(self, **_: Any) -> "FakeChatModel":
        return self


def _stream_node_names(compiled_graph: Any, initial_state: dict[str, object]) -> list[str]:
    node_names: list[str] = []
    for step in compiled_graph.stream(initial_state):
        node_names.extend(step.keys())
    return node_names


def _compiled_graph_edges(compiled_graph: Any) -> set[tuple[str, str]]:
    """返回编译后图的静态边集合，用于断言节点拓扑是否完整。"""

    return {(edge.source, edge.target) for edge in compiled_graph.get_graph().edges}


@pytest.mark.core_agent_tests
@pytest.mark.parametrize(
    ("router_output", "expected_node"),
    [
        ('{"route":"jd_parse","confidence":0.9,"reason":"jd","task_queue":[]}', "jd_parser"),
        ('{"route":"resume_match","confidence":0.9,"reason":"match","task_queue":[]}', "resume_matcher"),
        ('{"route":"mock_interview","confidence":0.9,"reason":"interview","task_queue":[]}', "interview_simulator"),
        ('{"route":"clarify","confidence":1.0,"reason":"clarify","task_queue":[]}', "clarify_node"),
        ('{"route":"out_of_scope","confidence":1.0,"reason":"oos","task_queue":[]}', "out_of_scope_node"),
    ],
)
def test_compiled_graph_routes_to_expected_node(router_output: str, expected_node: str) -> None:
    graph = build_graph(FakeChatModel(router_output))

    initial_state = {"user_input": "测试输入"}
    if expected_node == "jd_parser":
        initial_state = {"user_input": "某后端岗位，要求熟悉 Python，负责接口设计与性能优化。"}

    node_names = _stream_node_names(graph, initial_state)

    assert "supervisor" in node_names
    assert expected_node in node_names


@pytest.mark.core_agent_tests
def test_compiled_graph_handles_none_route_decision_via_error_node() -> None:
    graph = build_graph(FakeChatModel('{"route":"jd_parse","confidence":0.9,"reason":"unused","task_queue":[]}'))
    oversized_input = "x" * (MAX_INPUT_LENGTH + 1)

    node_names = _stream_node_names(graph, {"user_input": oversized_input})

    assert "supervisor" in node_names
    assert "error_node" in node_names
    assert "jd_parser" not in node_names
    assert "resume_matcher" not in node_names
    assert "interview_simulator" not in node_names


@pytest.mark.core_agent_tests
def test_invalid_route_never_enters_worker_placeholder_nodes() -> None:
    graph = build_graph(FakeChatModel('{"route":"unknown","confidence":0.9,"reason":"bad","task_queue":[]}'))

    node_names = _stream_node_names(graph, {"user_input": "测试非法路由"})

    assert "supervisor" in node_names
    assert "error_node" in node_names
    assert "jd_parser" not in node_names
    assert "resume_matcher" not in node_names
    assert "interview_simulator" not in node_names


@pytest.mark.core_agent_tests
@pytest.mark.parametrize(
    "terminal_node",
    [
        "jd_parser",
        "resume_matcher",
        "interview_simulator",
        "clarify_node",
        "out_of_scope_node",
        "error_node",
    ],
)
def test_compiled_graph_declares_explicit_end_edge_for_terminal_nodes(terminal_node: str) -> None:
    graph = build_graph(FakeChatModel('{"route":"jd_parse","confidence":0.9,"reason":"unused","task_queue":[]}'))

    edges = _compiled_graph_edges(graph)

    assert (terminal_node, END) in edges
