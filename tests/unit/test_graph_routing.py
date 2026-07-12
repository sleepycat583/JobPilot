"""路由函数与控制节点测试。"""

import pytest

from app.graph.control_nodes import CONTROL_MESSAGES, clarify_node, error_node, get_control_message, out_of_scope_node
from app.graph.routing import ROUTE_NODE_MAP, resolve_route_node, route_key
from app.schemas.router import RouterDecision


@pytest.mark.core_agent_tests
def test_route_key_and_resolve_route_node_for_valid_route() -> None:
    state = {
        "route_decision": RouterDecision(
            route="jd_parse",
            confidence=0.9,
            reason="JD request",
            task_queue=[],
        )
    }

    assert route_key(state) == "jd_parse"
    assert resolve_route_node(state) == "jd_parser"


@pytest.mark.core_agent_tests
def test_route_decision_none_resolves_to_error_node_without_attribute_error() -> None:
    state = {"route_decision": None}

    key = route_key(state)
    node = resolve_route_node(state)

    assert key == "error"
    assert node == "error_node"


@pytest.mark.core_agent_tests
def test_unknown_route_falls_back_to_error_node() -> None:
    class FakeDecision:
        route = "unknown"

    state = {"route_decision": FakeDecision()}

    assert resolve_route_node(state) == "error_node"


@pytest.mark.core_agent_tests
def test_control_messages_cover_required_nodes() -> None:
    assert "clarify_node" in CONTROL_MESSAGES
    assert "out_of_scope_node" in CONTROL_MESSAGES
    assert get_control_message("clarify_node")
    assert get_control_message("out_of_scope_node")


@pytest.mark.core_agent_tests
def test_control_nodes_are_deterministic_and_do_not_require_chat_model() -> None:
    clarify_result = clarify_node({})
    out_of_scope_result = out_of_scope_node({})
    error_result = error_node({})

    assert clarify_result["current_node"] == "clarify_node"
    assert out_of_scope_result["current_node"] == "out_of_scope_node"
    assert error_result["current_node"] == "error_node"


@pytest.mark.core_agent_tests
def test_route_node_map_matches_frozen_design() -> None:
    assert ROUTE_NODE_MAP == {
        "jd_parse": "jd_parser",
        "resume_match": "resume_matcher",
        "mock_interview": "interview_simulator",
        "clarify": "clarify_node",
        "out_of_scope": "out_of_scope_node",
        "error": "error_node",
    }
