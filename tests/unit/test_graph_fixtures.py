"""公共 Graph/State fixture 示例验证。

本文件只验证 Week3 新增的共享 fixture 本身可用，不重写既有测试覆盖面。
"""

from __future__ import annotations

from langgraph.types import Command

import pytest


@pytest.mark.core_agent_tests
def test_graph_test_graph_fixture_runs_jd_parse_and_returns_schema(graph_test_graph, graph_test_config) -> None:
    """验证公共 Graph fixture 可跑通一次 JD 解析，并产出真实 Schema。"""

    result = graph_test_graph.invoke(
        {"user_input": "某后端岗位，要求熟悉 Python，负责接口设计与性能优化。"},
        config=graph_test_config,
    )

    assert result["current_node"] == "prepare_final_review"
    assert result["review_status"] == "in_review"
    assert result["review_target"] == "jd_parsed"
    assert result["jd_parsed"].job_title == "后端工程师"
    assert result["jd_parsed"].skills[0].name == "Python"


@pytest.mark.core_agent_tests
def test_checkpoint_graph_test_factory_restores_same_interrupt_without_rebuilding_graph(
    checkpoint_graph_test_factory,
) -> None:
    """验证公共 SQLite Checkpoint fixture 可进入并恢复至少一种 HITL 场景。"""

    graph, model, config, _checkpoint_path = checkpoint_graph_test_factory(
        responses=['{"route":"mock_interview","confidence":0.95,"reason":"interview","task_queue":[]}'],
        thread_id="fixture-interview-thread",
    )

    started = graph.invoke({"user_input": "开始模拟面试"}, config=config)
    snapshot = graph.get_state(config)

    assert started["interview_state"].status == "waiting"
    assert snapshot.tasks[0].interrupts[0].value["type"] == "interview_answer"
    assert model.invoke_calls == 3

    resumed = graph.invoke(Command(resume={"action": "context_update", "context": "项目峰值QPS为1200"}), config=config)
    resumed_snapshot = graph.get_state(config)

    assert resumed["interview_state"].status == "waiting"
    assert resumed["interview_state"].user_context_updates[-1] == "项目峰值QPS为1200"
    assert resumed_snapshot.tasks[0].interrupts[0].value["type"] == "interview_answer"
    assert model.invoke_calls == 3