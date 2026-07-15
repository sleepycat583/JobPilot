"""Graph 最小拓扑测试。"""

from dataclasses import dataclass, field
from typing import Any

import pytest
from langgraph.graph import END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.constants import MAX_INPUT_LENGTH
from app.graph.builder import build_graph
from app.schemas.jd import JDParsed, SkillRequirement


@dataclass
class FakeChatModel:
    responses: list[Any]
    invoke_calls: int = 0
    prompts: list[str] = field(default_factory=list)

    def invoke(self, prompt: str) -> Any:
        self.prompts.append(prompt)
        response = self.responses[min(self.invoke_calls, len(self.responses) - 1)]
        self.invoke_calls += 1
        return response

    def bind(self, **_: Any) -> "FakeChatModel":
        return self


@dataclass
class FakeResumeStore:
    mapping: dict[tuple[str, str], list[dict[str, Any]]]

    def query(self, query_text: str, resume_version: str) -> list[dict[str, Any]]:
        return self.mapping.get((query_text, resume_version), [])


def _stream_node_names(compiled_graph: Any, initial_state: dict[str, object]) -> list[str]:
    node_names: list[str] = []
    for step in compiled_graph.stream(initial_state):
        node_names.extend(step.keys())
    return node_names


def _compiled_graph_edges(compiled_graph: Any) -> set[tuple[str, str]]:
    """返回编译后图的静态边集合，用于断言节点拓扑是否完整。"""

    return {(edge.source, edge.target) for edge in compiled_graph.get_graph().edges}


def _build_resume_match_jd() -> JDParsed:
    return JDParsed(
        job_title="Java后端工程师",
        seniority="mid",
        company_name=None,
        responsibilities=["API design"],
        skills=[SkillRequirement(name="Java", category="language", priority="must", evidence="精通 Java")],
        experience_requirements=["3年以上后端开发经验"],
        education_requirements=[],
        interview_focus=[],
        company_context=[],
        ambiguities=[],
        source_language="zh-CN",
    )


def _build_llm_analysis(responsibility_relevance: float) -> str:
    return (
        '{"must_items":[{"requirement":"Java","status":"transferable","rationale":"ok",'
        '"evidence":[{"chunk_id":"java-1","quote":"Java"}],"recent":true,"quantified":true}],'
        f'"responsibility_items":[{{"requirement":"API design","status":"transferable","rationale":"ok",'
        f'"evidence":[{{"chunk_id":"api-1","quote":"API design {responsibility_relevance}"}}],'
        '"recent":true,"quantified":true}],'
        '"preferred_items":[],'
        '"constraint_items":[{"requirement":"3年以上后端开发经验","status":"satisfied","rationale":"ok",'
        '"evidence":[{"chunk_id":"exp-1","quote":"3 years"}]}],'
        '"strengths":["strong"],"gaps":[],"recommendations":[]}'
    )


def _build_graph_for_resume_match(score: float) -> Any:
    responsibility_relevance = 0.4 if score < 60.0 else 0.4286
    return build_graph(
        FakeChatModel(
            [
                '{"route":"resume_match","confidence":0.9,"reason":"match","task_queue":[]}',
                _build_llm_analysis(responsibility_relevance),
            ]
        ),
        resume_store=FakeResumeStore(
            {
                ("Java", "2026-07-v1"): [{"chunk_id": "java-1", "quote": "Java", "relevance": 1.0}],
                ("API design", "2026-07-v1"): [
                    {
                        "chunk_id": "api-1",
                        "quote": f"API design {responsibility_relevance}",
                        "relevance": responsibility_relevance,
                    }
                ],
                ("3年以上后端开发经验", "2026-07-v1"): [
                    {"chunk_id": "exp-1", "quote": "3 years", "relevance": 1.0}
                ],
            }
        ),
    )


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
    graph = build_graph(FakeChatModel([router_output]))

    initial_state = {"user_input": "测试输入"}
    if expected_node == "jd_parser":
        initial_state = {"user_input": "某后端岗位，要求熟悉 Python，负责接口设计与性能优化。"}

    node_names = _stream_node_names(graph, initial_state)

    assert "supervisor" in node_names
    assert expected_node in node_names


@pytest.mark.core_agent_tests
def test_compiled_graph_handles_none_route_decision_via_error_node() -> None:
    graph = build_graph(FakeChatModel(['{"route":"jd_parse","confidence":0.9,"reason":"unused","task_queue":[]}']))
    oversized_input = "x" * (MAX_INPUT_LENGTH + 1)

    node_names = _stream_node_names(graph, {"user_input": oversized_input})

    assert "supervisor" in node_names
    assert "error_node" in node_names
    assert "jd_parser" not in node_names
    assert "resume_matcher" not in node_names
    assert "interview_simulator" not in node_names


@pytest.mark.core_agent_tests
def test_invalid_route_never_enters_worker_placeholder_nodes() -> None:
    graph = build_graph(FakeChatModel(['{"route":"unknown","confidence":0.9,"reason":"bad","task_queue":[]}']))

    node_names = _stream_node_names(graph, {"user_input": "测试非法路由"})

    assert "supervisor" in node_names
    assert "error_node" in node_names
    assert "jd_parser" not in node_names
    assert "resume_matcher" not in node_names
    assert "interview_simulator" not in node_names


@pytest.mark.core_agent_tests
@pytest.mark.parametrize(
    ("source_node", "expected_target"),
    [
        ("jd_parser", "queue_dispatch"),
        ("resume_matcher", "prepare_low_score_review"),
        ("interview_simulator", "interview_await_answer"),
        ("clarify_node", END),
        ("out_of_scope_node", END),
        ("error_node", END),
    ],
)
def test_compiled_graph_declares_expected_terminal_or_gate_edge(
    source_node: str,
    expected_target: str,
) -> None:
    """验证原有终点节点及 matcher 新增 Gate 的独立静态边。"""

    graph = build_graph(FakeChatModel(['{"route":"jd_parse","confidence":0.9,"reason":"unused","task_queue":[]}']))

    edges = _compiled_graph_edges(graph)

    assert (source_node, expected_target) in edges


@pytest.mark.core_agent_tests
def test_compiled_graph_declares_finalize_end_edge() -> None:
    graph = build_graph(FakeChatModel(['{"route":"jd_parse","confidence":0.9,"reason":"unused","task_queue":[]}']))

    edges = _compiled_graph_edges(graph)

    assert ("finalize_node", END) in edges


@pytest.mark.core_agent_tests
def test_low_score_gate_sets_review_status_and_stops_before_finalize() -> None:
    graph = build_graph(
        FakeChatModel(
            [
                '{"route":"resume_match","confidence":0.9,"reason":"match","task_queue":[]}',
                _build_llm_analysis(0.4),
            ]
        ),
        resume_store=FakeResumeStore(
            {
                ("Java", "2026-07-v1"): [{"chunk_id": "java-1", "quote": "Java", "relevance": 1.0}],
                ("API design", "2026-07-v1"): [{"chunk_id": "api-1", "quote": "API design 0.4", "relevance": 0.4}],
                ("3年以上后端开发经验", "2026-07-v1"): [{"chunk_id": "exp-1", "quote": "3 years", "relevance": 1.0}],
            }
        ),
        checkpointer=MemorySaver(),
    )
    config = {"configurable": {"thread_id": "low-score-test"}}

    result = graph.invoke(
        {
            "user_input": "测试低分 Gate",
            "resume_version": "2026-07-v1",
            "jd_parsed": _build_resume_match_jd(),
        },
        config=config,
    )

    assert result["match_result"].total_score == 59.9
    assert result["match_result"].low_score_review_required is True
    assert result["review_status"] == "in_review"
    assert result["review_target"] == "match_result"
    assert result["current_node"] == "prepare_low_score_review"
    snapshot = graph.get_state(config)
    assert snapshot.tasks[0].interrupts[0].value["type"] == "low_match_score"
    assert "finalize_node" not in [event["node"] for event in result["execution_history"]]
    assert result.get("final_output") is None

    resumed = graph.invoke(Command(resume={"action": "continue", "feedback": ""}), config=config)
    assert resumed["review_status"] == "in_review"
    assert resumed["current_node"] == "prepare_final_review"
    assert graph.get_state(config).tasks[0].interrupts[0].value["type"] == "final_review"

    approved = graph.invoke(Command(resume={"action": "approve"}), config=config)
    assert approved["review_status"] == "approved"
    assert approved["current_node"] == "finalize_node"
    assert approved["final_output"]["type"] == "match_result"


@pytest.mark.core_agent_tests
def test_high_score_match_requires_final_approval_before_output() -> None:
    graph = _build_graph_for_resume_match(60.0)

    result = graph.invoke(
        {
            "user_input": "测试高分 Gate",
            "resume_version": "2026-07-v1",
            "jd_parsed": _build_resume_match_jd(),
        }
    )

    assert result["match_result"].total_score == 60.0
    assert result["match_result"].low_score_review_required is False
    assert result["review_status"] == "in_review"
    assert result["current_node"] == "prepare_final_review"
    assert any(event["node"] == "low_score_gate" for event in result["execution_history"])
    assert result.get("final_output") is None


@pytest.mark.core_agent_tests
def test_revise_inputs_checkpoint_history_preserves_full_review_lifecycle() -> None:
    """验证低分重算在每个审核状态边界留下可审计 checkpoint。"""

    graph = build_graph(
        FakeChatModel(
            [
                '{"route":"resume_match","confidence":0.9,"reason":"match","task_queue":[]}',
                _build_llm_analysis(0.4),
                _build_llm_analysis(0.4286),
            ]
        ),
        resume_store=FakeResumeStore(
            {
                ("Java", "2026-07-v1"): [{"chunk_id": "java-1", "quote": "Java", "relevance": 1.0}],
                ("API design", "2026-07-v1"): [{"chunk_id": "api-1", "quote": "API design 0.4", "relevance": 0.4}],
                ("3年以上后端开发经验", "2026-07-v1"): [{"chunk_id": "exp-1", "quote": "3 years", "relevance": 1.0}],
                ("Java", "2026-07-v2"): [{"chunk_id": "java-1", "quote": "Java", "relevance": 1.0}],
                ("API design", "2026-07-v2"): [{"chunk_id": "api-1", "quote": "API design 0.4286", "relevance": 0.4286}],
                ("3年以上后端开发经验", "2026-07-v2"): [{"chunk_id": "exp-1", "quote": "3 years", "relevance": 1.0}],
            }
        ),
        checkpointer=MemorySaver(),
    )
    config = {"configurable": {"thread_id": "revise-history-test"}}
    graph.invoke(
        {
            "user_input": "测试低分重算审核轨迹",
            "resume_version": "2026-07-v1",
            "jd_parsed": _build_resume_match_jd(),
        },
        config=config,
    )
    graph.invoke(
        Command(resume={"action": "revise_inputs", "resume_version": "2026-07-v2", "feedback": "使用最新简历"}),
        config=config,
    )

    history = list(reversed(list(graph.get_state_history(config))))
    lifecycle = [
        (snapshot.values.get("review_status"), snapshot.values.get("match_result"))
        for snapshot in history
    ]
    in_review_positions = [index for index, (status, _) in enumerate(lifecycle) if status == "in_review"]
    rejected_index = next(index for index, (status, _) in enumerate(lifecycle) if status == "rejected")
    revising_index = next(index for index, (status, result) in enumerate(lifecycle) if status == "revising" and result is None)
    pending_index = next(index for index, (status, _) in enumerate(lifecycle) if index > revising_index and status == "pending")

    assert in_review_positions[0] < rejected_index < revising_index < pending_index < in_review_positions[-1]


@pytest.mark.core_agent_tests
def test_interview_skeleton_waits_for_answer_and_does_not_enter_final_review() -> None:
    graph = build_graph(FakeChatModel(['{"route":"mock_interview","confidence":0.9,"reason":"interview","task_queue":[]}']), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "interview-wait-test"}}

    result = graph.invoke({"user_input": "开始模拟面试"}, config=config)

    assert result["current_node"] == "interview_simulator"
    interview_state = result["interview_state"]
    assert interview_state.status == "waiting"
    assert interview_state.current_question_id == "skeleton-q1"
    snapshot = graph.get_state(config)
    assert snapshot.tasks[0].interrupts[0].value["type"] == "interview_answer"
    assert result.get("final_output") is None


@pytest.mark.core_agent_tests
def test_interview_context_update_reinterrupts_same_question() -> None:
    graph = build_graph(FakeChatModel(['{"route":"mock_interview","confidence":0.9,"reason":"interview","task_queue":[]}']), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "interview-context-test"}}
    graph.invoke({"user_input": "开始模拟面试"}, config=config)

    resumed = graph.invoke(Command(resume={"action": "context_update", "context": "项目峰值QPS为1200"}), config=config)

    assert resumed["interview_state"].status == "waiting"
    assert resumed["interview_state"].user_context_updates[-1] == "项目峰值QPS为1200"
    assert graph.get_state(config).tasks[0].interrupts[0].value["question_id"] == "skeleton-q1"


@pytest.mark.core_agent_tests
def test_interview_submit_answer_transitions_to_evaluating_and_ends() -> None:
    graph = build_graph(FakeChatModel(['{"route":"mock_interview","confidence":0.9,"reason":"interview","task_queue":[]}']), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "interview-answer-test"}}
    graph.invoke({"user_input": "开始模拟面试"}, config=config)

    resumed = graph.invoke(Command(resume={"action": "submit_answer", "answer": "我负责过缓存优化项目。"}), config=config)

    assert resumed["interview_state"].status == "evaluating"
    assert resumed["interview_state"].question_records[0].answer == "我负责过缓存优化项目。"
    assert resumed["current_node"] == "interview_await_answer"


@pytest.mark.core_agent_tests
def test_interview_end_marks_completed_without_report() -> None:
    graph = build_graph(FakeChatModel(['{"route":"mock_interview","confidence":0.9,"reason":"interview","task_queue":[]}']), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "interview-end-test"}}
    graph.invoke({"user_input": "开始模拟面试"}, config=config)

    resumed = graph.invoke(Command(resume={"action": "end_interview"}), config=config)

    assert resumed["interview_state"].status == "completed"
    assert resumed["interview_state"].current_question_id is None
    assert resumed["interview_state"].report is None