import os
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from app.core.config import Settings, configure_langsmith
from app.core.observability import TRACE_SCHEMA_VERSION, build_trace_config
from app.graph import build_career_graph, build_model_bundle
from app.graph.builder import WORKER_DESCRIPTIONS
from app.graph.models import ModelBundle, StubSupervisorModel
from app.graph.prompts import STUB_OUTPUTS, SUPERVISOR_PROMPT, build_supervisor_prompt
from app.graph.state import WORKER_WRITABLE_FIELDS, WorkerName
from app.graph.workers import build_worker_update, finalize_supervisor_step, sanitize_output


@pytest.mark.parametrize("worker", list(WORKER_DESCRIPTIONS))
def test_official_supervisor_hands_off_to_exactly_one_worker(worker: WorkerName) -> None:
    models = ModelBundle(mode="stub", supervisor_model=StubSupervisorModel(route=worker), worker_model=None)
    with SqliteSaver.from_conn_string(":memory:") as saver:
        graph = build_career_graph(models, saver)
        result = graph.invoke(
            {
                "messages": [HumanMessage(content="同一段输入不参与 Stub 路由判断")],
                "remaining_steps": 30,
                "readonly_context": {"latest_user_message": "同一段输入不参与 Stub 路由判断"},
                "run_id": str(uuid4()),
                "route_audit": None,
                "worker_name": None,
                "worker_result": None,
                "visible_output": None,
                "public_output": None,
            },
            {"configurable": {"thread_id": str(uuid4())}},
        )
    assert result["worker_name"] == worker
    assert result["route_audit"]["worker"] == worker
    assert result["public_output"] == STUB_OUTPUTS[worker]
    assert "FINISH" not in result["public_output"]
    assert "tool_call" not in result["public_output"]


def test_graph_contains_supervisor_and_all_worker_nodes() -> None:
    models = ModelBundle(mode="stub", supervisor_model=StubSupervisorModel(), worker_model=None)
    with SqliteSaver.from_conn_string(":memory:") as saver:
        graph = build_career_graph(models, saver)
        node_names = set(graph.get_graph().nodes)
    assert {"supervisor", *WORKER_DESCRIPTIONS}.issubset(node_names)


def test_worker_and_output_nodes_respect_state_ownership() -> None:
    models = ModelBundle(mode="stub", supervisor_model=StubSupervisorModel(), worker_model=None)
    update = build_worker_update("chat_worker", {"readonly_context": {}}, models)
    output_update = sanitize_output({"visible_output": "用户可见结果\x00"})
    assert set(update).issubset(WORKER_WRITABLE_FIELDS)
    assert set(output_update) == {"public_output"}
    assert output_update["public_output"] == "用户可见结果"


def test_sanitizer_extracts_public_field_and_hides_internal_envelope() -> None:
    output = sanitize_output(
        {"visible_output": '{"action":"run_match","message":"匹配分析已完成。","worker_result":{"secret":true}}'}
    )
    assert output["public_output"] == "匹配分析已完成。"
    blocked = sanitize_output({"visible_output": '{"tool_calls":[{"name":"transfer_to_match_worker"}]}'})
    assert blocked["public_output"] == "暂时无法生成有效结果，请稍后重试。"
    unknown = sanitize_output({"visible_output": '{"internal":"not-for-users"}'})
    assert unknown["public_output"] == "暂时无法生成有效结果，请稍后重试。"
    fenced = sanitize_output({"visible_output": '```json\n{"message":"已完成分析"}\n```'})
    assert fenced["public_output"] == "已完成分析"


def test_trace_config_contains_only_non_content_metadata() -> None:
    config = build_trace_config("thread-123", "run-456", operation="conversation_turn")
    assert config["metadata"] == {
        "app_thread_id": "thread-123",
        "run_id": "run-456",
        "operation": "conversation_turn",
        "trace_schema_version": TRACE_SCHEMA_VERSION,
    }
    assert "content" not in str(config["metadata"])
    assert config["run_name"] == "career.conversation_turn"


def test_supervisor_post_hook_prevents_second_handoff_after_worker_returns() -> None:
    repeated_handoff = AIMessage(
        content="",
        name="supervisor",
        tool_calls=[
            {
                "name": "transfer_to_jd_worker",
                "args": {"confidence": 1.0, "reason": "repeat"},
                "id": str(uuid4()),
                "type": "tool_call",
            }
        ],
    )
    update = finalize_supervisor_step(
        {
            "messages": [repeated_handoff],
            "worker_name": "jd_worker",
            "visible_output": "已完成 JD 解析",
        }
    )
    final_message = update["messages"][0]
    assert final_message.content == "FINISH"
    assert final_message.tool_calls == []
    assert update["public_output"] == "已完成 JD 解析"


def test_supervisor_prompt_enforces_route_only_boundary() -> None:
    assert "唯一的职责" in SUPERVISOR_PROMPT
    assert "不能回答用户问题" in SUPERVISOR_PROMPT
    assert "且只调用一个" in SUPERVISOR_PROMPT
    assert "不设置阈值" in SUPERVISOR_PROMPT
    assert "即使资料缺失" in SUPERVISOR_PROMPT
    assert "明确结束面试" in SUPERVISOR_PROMPT


def test_supervisor_receives_active_interview_routing_context() -> None:
    messages = build_supervisor_prompt(
        {
            "messages": [HumanMessage(content="这是我的回答")],
            "readonly_context": {
                "latest_user_message": "这是我的回答",
                "active_interview": {"phase": "question", "current_question": "请介绍项目"},
                "pending_interrupt": {"type": "interview_answer", "title": "第 1 题"},
            },
        }
    )
    assert "interview_answer" in str(messages[0].content)
    assert messages[-1].content == "这是我的回答"


def test_openai_mode_requires_environment_key() -> None:
    settings = Settings(llm_mode="openai", openai_api_key=None, _env_file=None)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        build_model_bundle(settings)


def test_openai_mode_passes_compatible_base_url_to_chat_model() -> None:
    from pydantic import SecretStr

    settings = Settings(
        llm_mode="openai",
        openai_api_key=SecretStr("test-key"),
        openai_base_url="https://example.test/v1",
        _env_file=None,
    )
    model = build_model_bundle(settings).supervisor_model
    assert getattr(model, "openai_api_base") == "https://example.test/v1"


def test_langsmith_settings_are_injected_without_exposing_value(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import SecretStr

    settings = Settings(
        langsmith_tracing=True,
        langsmith_project="test-project",
        langsmith_api_key=SecretStr("test-langsmith-key"),
        _env_file=None,
    )
    configure_langsmith(settings)
    assert os.environ["LANGSMITH_TRACING"] == "true"
    assert os.environ["LANGCHAIN_TRACING_V2"] == "true"
    assert os.environ["LANGSMITH_PROJECT"] == "test-project"
    assert os.environ["LANGSMITH_API_KEY"] == "test-langsmith-key"
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)
