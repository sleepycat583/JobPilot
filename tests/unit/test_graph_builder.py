"""Graph 最小拓扑测试。"""

from dataclasses import dataclass, field
from time import sleep
from typing import Any

import pytest
from langgraph.graph import END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.constants import MAX_INPUT_LENGTH
from app.graph.builder import build_graph
from app.graph.builder import (
    _guard_route,
    _resolve_finalize_route,
    _resolve_final_review_route,
    _resolve_interview_decision_route,
    _resolve_interview_resume_route,
    _resolve_jd_completion_route,
    _resolve_low_score_gate_route,
    _resolve_match_result_route,
    _resolve_queue_dispatch_route,
    _resolve_revision_target_route,
)
from app.graph.routing import resolve_route_node
from app.agents.interview_simulator import _assert_question_records_unchanged, interview_decision_node
from app.schemas.interview import InterviewState, InterviewTopicPlan, QuestionRecord
from app.schemas.jd import JDParsed, SkillRequirement
from app.schemas.resume import MatchUnavailableResult
from app.schemas.router import RouterDecision
from app.graph.control_nodes import finalize_node


@dataclass
class FakeChatModel:
    responses: list[Any]
    invoke_calls: int = 0
    prompts: list[str] = field(default_factory=list)

    def invoke(self, prompt: str) -> Any:
        self.prompts.append(prompt)
        response = self.responses[self.invoke_calls] if self.invoke_calls < len(self.responses) else self._interview_response(prompt)
        self.invoke_calls += 1
        if isinstance(response, Exception):
            raise response
        return response

    def bind(self, **_: Any) -> "FakeChatModel":
        return self

    def _interview_response(self, prompt: str) -> str:
        if "Question IDs: []" in prompt or "records: []" in prompt or "overall: 0.0" in prompt:
            return '{"performance_summary":"尚无有效回答，样本不足。","recurring_strengths":[],"recurring_weaknesses":[],"review_actions":[],"question_references":[]}'
        if "InterviewPlanOutput" in prompt:
            return '{"plan":[{"topic_id":"project","topic":"项目经历","objective":"考察项目贡献","priority":"core","basis":"user_goal"},{"topic_id":"foundation","topic":"技术基础","objective":"考察技术基础","priority":"core","basis":"user_goal"}]}'
        if "QuestionProposal" in prompt:
            if "'question_id': 'q-1'" in prompt:
                return '{"topic":"技术基础","question":"请解释一次性能排查过程。"}'
            return '{"topic":"项目经历","question":"请介绍一个你负责的项目。"}'
        if "AnswerEvaluation" in prompt:
            return '{"scores":{"technical_accuracy":70,"structure":70,"job_relevance":70,"evidence":70},"feedback":"ok","strengths":[],"issues":[],"answer_relevance":"on_topic","fatal_error":false,"fatal_error_reason":null}'
        if "InterviewReportNarrative" in prompt:
            return '{"performance_summary":"样本不足。","recurring_strengths":[],"recurring_weaknesses":[],"review_actions":[],"question_references":["q-1"]}'
        return self.responses[-1]


@dataclass
class FakeResumeStore:
    mapping: dict[tuple[str, str], list[dict[str, Any]]]

    def query(self, query_text: str, resume_id: str) -> list[dict[str, Any]]:
        return self.mapping.get((query_text, resume_id), [])


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


@pytest.mark.core_agent_tests
@pytest.mark.parametrize(
    ("node_name", "resolver", "state", "expected"),
    [
        ("rolling_summary", lambda _: "supervisor", {}, "supervisor"),
        ("supervisor", resolve_route_node, {"route_decision": RouterDecision(route="clarify", confidence=1.0, reason="ok", task_queue=[])}, "clarify_node"),
        ("queue_dispatch", _resolve_queue_dispatch_route, {"current_node": "jd_parse"}, "jd_parser"),
        ("jd_parser", _resolve_jd_completion_route, {"error_log": []}, "prepare_review"),
        ("resume_matcher", _resolve_match_result_route, {"match_result": type("Match", (), {"low_score_review_required": False})()}, "prepare_review"),
        ("low_score_gate", _resolve_low_score_gate_route, {"review_status": "pending"}, "continue"),
        ("prepare_low_score_review", lambda _: "low_score_gate", {}, "low_score_gate"),
        ("low_score_cancelled", lambda _: "end", {}, "end"),
        ("prepare_final_review", lambda _: "final_review_gate", {}, "final_review_gate"),
        ("final_review_gate", _resolve_final_review_route, {"review_status": "approved"}, "approve"),
        ("revision_dispatch", _resolve_revision_target_route, {"review_target": "match_result"}, "resume_matcher"),
        ("finalize_node", _resolve_finalize_route, {"task_queue": []}, "end"),
        ("interview_simulator", lambda _: "ask_question", {}, "ask_question"),
        ("ask_question", lambda _: "interview_await_answer", {}, "interview_await_answer"),
        ("interview_await_answer", _resolve_interview_resume_route, {"interview_state": InterviewState(status="waiting", target_question_count=1, current_question_id="q-1", question_records=[], user_context_updates=[], report=None, plan=[])}, "wait"),
        ("evaluate_answer", lambda _: "interview_decision", {}, "interview_decision"),
        ("interview_decision", _resolve_interview_decision_route, {"interview_next_action": "finish"}, "report"),
        ("interview_decision", _resolve_interview_decision_route, {"interview_next_action": "retry_evaluation"}, "retry_evaluation"),
        ("generate_review_report", lambda _: "prepare_final_review", {}, "prepare_final_review"),
        ("clarify_node", lambda _: "end", {}, "end"),
        ("out_of_scope_node", lambda _: "end", {}, "end"),
    ],
)
def test_error_guard_preserves_real_normal_route_for_all_non_error_nodes(
    node_name: str, resolver: Any, state: dict[str, Any], expected: str
) -> None:
    """20 个节点以真实正常 State 跑原 resolver，guard 结果必须完全不变。"""

    assert resolver(state) == expected
    assert _guard_route(node_name, resolver)(state) == expected


@pytest.mark.core_agent_tests
def test_error_guard_routes_only_matching_latest_unhandled_error_to_error_node() -> None:
    state = {"error_log": [{"code": "UNHANDLED_NODE_EXCEPTION", "node": "jd_parser"}]}

    assert _guard_route("jd_parser", lambda _: "prepare_review")(state) == "error"


@pytest.mark.core_agent_tests
def test_match_result_none_routes_to_error_node() -> None:
    """resume_matcher 返回 match_result=None 时必须路由到 error_node，而非复用旧 review_target 进入审核。"""

    assert _resolve_match_result_route({"match_result": None}) == "error"


@pytest.mark.core_agent_tests
def test_match_unavailable_routes_to_final_review_and_finalizes_serializable_draft() -> None:
    unavailable = MatchUnavailableResult(
        status="MATCH_UNAVAILABLE",
        resume_id="v1",
        retrieval_evidence=[],
        message="请人工检查检索证据",
    )

    assert _resolve_match_result_route({"match_result": unavailable}) == "prepare_review"
    update = finalize_node(
        {
            "match_result": unavailable,
            "review_status": "approved",
            "review_target": "match_result",
            "task_queue": [],
        }
    )

    assert update["final_output"]["content"]["status"] == "MATCH_UNAVAILABLE"
    assert "total_score" not in update["final_output"]["content"]


@pytest.mark.core_agent_tests
def test_unhandled_supervisor_exception_persists_then_enters_error_node_without_worker() -> None:
    """120ms 终态异常必须先持久化，再阻断原有 queue_dispatch/Worker 正常路径。"""

    class DelayedFailure(RuntimeError):
        pass

    model = FakeChatModel([DelayedFailure("unhandled supervisor failure")])
    original_invoke = model.invoke

    def delayed_invoke(prompt: str) -> Any:
        sleep(0.12)
        return original_invoke(prompt)

    model.invoke = delayed_invoke  # type: ignore[method-assign]
    graph = build_graph(model)

    result = graph.invoke({"user_input": "分析这个岗位"})

    assert result["current_node"] == "error_node"
    assert result["error_log"][-1]["code"] == "UNHANDLED_NODE_EXCEPTION"
    assert result["error_log"][-1]["node"] == "supervisor"
    assert result["execution_history"][-1]["event"] == "error"
    assert not {"queue_dispatch", "jd_parser", "resume_matcher", "interview_simulator"} & {
        event["node"] for event in result["execution_history"]
    }


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
        ("jd_parser", "prepare_final_review"),
        ("resume_matcher", "prepare_final_review"),
        ("interview_simulator", "ask_question"),
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
def test_compiled_graph_declares_finalize_queue_or_end_edges() -> None:
    graph = build_graph(FakeChatModel(['{"route":"jd_parse","confidence":0.9,"reason":"unused","task_queue":[]}']))

    edges = _compiled_graph_edges(graph)

    assert ("finalize_node", "queue_dispatch") in edges
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
            "resume_id": "2026-07-v1",
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
            "resume_id": "2026-07-v1",
            "jd_parsed": _build_resume_match_jd(),
        }
    )

    assert result["match_result"].total_score == 60.0
    assert result["match_result"].low_score_review_required is False
    assert result["review_status"] == "in_review"
    assert result["current_node"] == "prepare_final_review"
    assert not any(event["node"] == "low_score_gate" for event in result["execution_history"])
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
            "resume_id": "2026-07-v1",
            "jd_parsed": _build_resume_match_jd(),
        },
        config=config,
    )
    graph.invoke(
        Command(resume={"action": "revise_inputs", "resume_id": "2026-07-v2", "feedback": "使用最新简历"}),
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
def test_interview_plan_asks_first_question_with_default_eight_and_does_not_enter_final_review() -> None:
    graph = build_graph(FakeChatModel(['{"route":"mock_interview","confidence":0.9,"reason":"interview","task_queue":[]}']), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "interview-wait-test"}}

    result = graph.invoke({"user_input": "开始模拟面试"}, config=config)

    assert result["current_node"] == "ask_question"
    interview_state = result["interview_state"]
    assert interview_state.status == "waiting"
    assert interview_state.target_question_count == 8
    assert interview_state.current_question_id == "q-1"
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
    assert graph.get_state(config).tasks[0].interrupts[0].value["question_id"] == "q-1"


@pytest.mark.core_agent_tests
def test_interview_submit_answer_evaluates_and_waits_for_next_question() -> None:
    graph = build_graph(FakeChatModel(['{"route":"mock_interview","confidence":0.9,"reason":"interview","task_queue":[]}']), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "interview-answer-test"}}
    graph.invoke({"user_input": "开始模拟面试"}, config=config)

    resumed = graph.invoke(Command(resume={"action": "submit_answer", "answer": "我负责过缓存优化项目。"}), config=config)

    assert resumed["interview_state"].status == "waiting"
    assert resumed["interview_state"].question_records[0].answer == "我负责过缓存优化项目。"
    assert resumed["interview_state"].question_records[0].scores["technical_accuracy"] == 70.0
    assert resumed["current_node"] == "ask_question"


@pytest.mark.core_agent_tests
def test_interview_evaluation_retry_reuses_original_record_without_question_count_growth() -> None:
    class EvaluationFailsThenSucceedsModel(FakeChatModel):
        evaluation_calls: int = 0

        def _interview_response(self, prompt: str) -> str:
            if "AnswerEvaluation" in prompt:
                self.evaluation_calls += 1
                if self.evaluation_calls <= 3:
                    return '{"bad":"json"}'
            return super()._interview_response(prompt)

    graph = build_graph(EvaluationFailsThenSucceedsModel(['{"route":"mock_interview","confidence":0.9,"reason":"interview","task_queue":[]}']), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "interview-evaluation-retry-test"}}
    graph.invoke({"user_input": "开始模拟面试"}, config=config)

    unavailable = graph.invoke(Command(resume={"action": "submit_answer", "answer": "我负责过缓存优化项目。"}), config=config)
    assert unavailable["interview_state"].question_records[0].evaluation_status == "unavailable"
    assert graph.get_state(config).tasks[0].interrupts[0].value == {
        "type": "interview_evaluation_unavailable",
        "target": "question_record",
        "question_id": "q-1",
        "accepted_actions": ["retry_evaluation", "skip_evaluation"],
    }

    retried = graph.invoke(Command(resume={"action": "retry_evaluation"}), config=config)
    records = retried["interview_state"].question_records
    assert [record.question_id for record in records] == ["q-1", "q-2"]
    assert records[0].evaluation_status == "available"
    assert records[0].answer == "我负责过缓存优化项目。"
    assert records[0].scores["technical_accuracy"] == 70.0


@pytest.mark.core_agent_tests
def test_all_unavailable_evaluations_keep_null_scores_and_enter_final_review_gate() -> None:
    """零个可聚合样本仍必须进入既有最终核可，而不是绕过 Review Gate。"""

    class EvaluationAndReportFailModel(FakeChatModel):
        def _interview_response(self, prompt: str) -> str:
            if "AnswerEvaluation" in prompt or "InterviewReportNarrative" in prompt:
                return '{"bad":"json"}'
            return super()._interview_response(prompt)

    graph = build_graph(EvaluationAndReportFailModel(['{"route":"mock_interview","confidence":0.9,"reason":"interview","task_queue":[]}']), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "interview-zero-score-review-test"}}
    graph.invoke({"user_input": "开始模拟面试"}, config=config)
    graph.invoke(Command(resume={"action": "submit_answer", "answer": "我负责过缓存优化项目。"}), config=config)

    review = graph.invoke(Command(resume={"action": "skip_evaluation"}), config=config)
    assert graph.get_state(config).tasks[0].interrupts[0].value["type"] == "interview_answer"

    final_review = graph.invoke(Command(resume={"action": "end_interview"}), config=config)
    report = final_review["interview_state"].report
    assert report is not None
    assert report.scoring_status == "unavailable"
    assert report.overall_score is None
    assert report.dimension_scores is None
    assert final_review["review_status"] == "in_review"
    assert final_review["review_target"] == "interview_report"
    assert graph.get_state(config).tasks[0].interrupts[0].value["type"] == "final_review"


@pytest.mark.core_agent_tests
def test_interview_end_generates_report_and_enters_final_review() -> None:
    graph = build_graph(FakeChatModel(['{"route":"mock_interview","confidence":0.9,"reason":"interview","task_queue":[]}']), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "interview-end-test"}}
    graph.invoke({"user_input": "开始模拟面试"}, config=config)

    resumed = graph.invoke(Command(resume={"action": "end_interview"}), config=config)

    assert resumed["interview_state"].status == "completed"
    assert resumed["interview_state"].current_question_id is None
    assert resumed["interview_state"].report is not None
    assert resumed["review_status"] == "in_review"
    assert resumed["review_target"] == "interview_report"
    assert resumed["final_output"] is None
    assert graph.get_state(config).tasks[0].interrupts[0].value["type"] == "final_review"


@pytest.mark.core_agent_tests
def test_interview_report_reject_regenerates_only_report_and_preserves_question_records() -> None:
    """驳回复盘不得回到出题或评价节点，逐题事实必须逐字保持不变。"""

    graph = build_graph(FakeChatModel(['{"route":"mock_interview","confidence":0.9,"reason":"interview","task_queue":[]}']), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "interview-report-reject-test"}}
    graph.invoke({"user_input": "开始模拟面试"}, config=config)
    graph.invoke(Command(resume={"action": "submit_answer", "answer": "我负责过缓存优化项目。"}), config=config)
    review = graph.invoke(Command(resume={"action": "end_interview"}), config=config)
    records_before = [record.model_dump(mode="json") for record in review["interview_state"].question_records]

    revised = graph.invoke(Command(resume={"action": "reject", "feedback": "请重写复习建议"}), config=config)

    assert revised["review_status"] == "in_review"
    assert revised["review_target"] == "interview_report"
    assert [record.model_dump(mode="json") for record in revised["interview_state"].question_records] == records_before
    history_nodes = [event["node"] for event in revised["execution_history"]]
    dispatch_index = history_nodes.index("revision_dispatch")
    assert set(history_nodes[dispatch_index + 1:]).issubset({"prepare_final_review", "final_review_gate"})
    assert not {"supervisor", "queue_dispatch", "interview_simulator", "ask_question", "interview_await_answer", "evaluate_answer", "interview_decision"} & set(history_nodes[dispatch_index + 1:])
    assert graph.get_state(config).tasks[0].interrupts[0].value["type"] == "final_review"

    lifecycle = [snapshot.values.get("review_status") for snapshot in reversed(list(graph.get_state_history(config)))]
    rejected_index = lifecycle.index("rejected")
    revising_index = lifecycle.index("revising")
    pending_index = lifecycle.index("pending", revising_index)
    assert rejected_index < revising_index < pending_index < len(lifecycle) - 1
    assert lifecycle[-1] == "in_review"


def test_report_record_invariant_rejects_modified_question_records() -> None:
    """运行时不变量防止未来报告节点实现越权改写逐题事实。"""

    original = QuestionRecord(
        question_id="q-1", topic="基础", question="问题", answer="回答", follow_up_of=None,
        scores={"technical_accuracy": 70.0, "structure": 70.0, "job_relevance": 70.0, "evidence": 70.0},
        feedback="ok", strengths=[], issues=[], answer_relevance="on_topic",
    )
    changed = original.model_copy(update={"answer": "被改写"})
    with pytest.raises(ValueError, match="must not modify"):
        _assert_question_records_unchanged([original.model_dump(mode="json")], InterviewState(
            status="completed", target_question_count=1, current_question_id=None,
            question_records=[changed], user_context_updates=[], report=None, plan=[],
        ))


@pytest.mark.core_agent_tests
def test_interview_graph_decision_routes_fifteenth_record_to_report_not_ask_question() -> None:
    """验证 15 题硬上限在 Graph 决策层阻止第 16 次 ask_question。"""

    records = [
        QuestionRecord(
            question_id=f"q-{index}",
            topic="项目经历",
            question=f"问题 {index}",
            answer="回答",
            follow_up_of=None,
            scores={"technical_accuracy": 70.0, "structure": 70.0, "job_relevance": 70.0, "evidence": 70.0},
            feedback="ok",
            strengths=[],
            issues=[],
            answer_relevance="on_topic",
        )
        for index in range(1, 16)
    ]
    state = {
        "interview_state": InterviewState(
            status="evaluating",
            target_question_count=15,
            current_question_id="q-15",
            question_records=records,
            user_context_updates=[],
            report=None,
            plan=[InterviewTopicPlan(topic_id="project", topic="项目经历", objective="考察项目", priority="core", basis="general")],
        )
    }

    update = interview_decision_node(state)

    assert update["interview_next_action"] == "finish"
    assert update["interview_completion_reason"] == "max_questions_reached"
    assert _resolve_interview_decision_route({**state, **update}) == "report"