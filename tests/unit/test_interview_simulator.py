"""模拟面试 LLM 业务函数的独立单元测试。"""

import json

import pytest

from app.agents.interview_simulator import (
    ask_question,
    build_interview_plan,
    evaluate_answer,
    generate_review_report,
    initialize_interview_state,
)
from app.schemas.interview import InterviewPlanOutput, InterviewState, InterviewTopicPlan, QuestionRecord
from app.schemas.jd import JDParsed, SkillRequirement
from app.schemas.resume import MatchResult


class FakeChatModel:
    """按调用顺序返回 JSON，并保留 Prompt 供来源约束断言。"""

    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = responses
        self.prompts: list[str] = []

    def invoke(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return json.dumps(self.responses.pop(0), ensure_ascii=False)


def _plan_payload(*, basis: str = "jd") -> dict[str, object]:
    return {
        "plan": [
            {"topic_id": "cache", "topic": "缓存", "objective": "考察缓存设计。", "priority": "core", "basis": basis},
            {"topic_id": "project", "topic": "项目经历", "objective": "考察项目贡献。", "priority": "core", "basis": basis},
        ]
    }


def _jd() -> JDParsed:
    return JDParsed(
        job_title="后端工程师",
        seniority="mid",
        company_name=None,
        responsibilities=["设计高并发 API"],
        skills=[SkillRequirement(name="Redis", category="database", priority="must", evidence="熟练 Redis")],
        experience_requirements=[],
        education_requirements=[],
        interview_focus=["缓存与性能"],
        company_context=[],
        ambiguities=[],
        source_language="zh-CN",
    )


def _match() -> MatchResult:
    return MatchResult(
        total_score=68.0,
        dimension_scores={},
        matched_items=[],
        strengths=["具备 Python 项目经验"],
        gaps=["缺少 Redis 性能优化案例"],
        recommendations=[],
        low_score_review_required=False,
        resume_version="v1",
    )


def _state(records: list[QuestionRecord] | None = None) -> InterviewState:
    return InterviewState(
        status="asking",
        target_question_count=8,
        current_question_id=None,
        question_records=records or [],
        user_context_updates=["峰值 QPS 为 1200"],
        report=None,
        plan=InterviewPlanOutput.model_validate(_plan_payload()).plan,
    )


def _evaluated_record(question_id: str = "q-1", topic: str = "缓存") -> QuestionRecord:
    return QuestionRecord(
        question_id=question_id,
        topic=topic,
        question="如何处理缓存击穿？",
        answer="我会使用互斥锁并监控热点键。",
        follow_up_of=None,
        scores={"technical_accuracy": 80.0, "structure": 70.0, "job_relevance": 80.0, "evidence": 60.0},
        feedback="回答正确。",
        strengths=["理解互斥锁。"],
        issues=[],
        answer_relevance="on_topic",
    )


@pytest.mark.core_agent_tests
def test_plan_uses_jd_and_match_inputs_in_prompt() -> None:
    model = FakeChatModel([_plan_payload()])

    result = build_interview_plan(model, user_goal="准备后端面试", jd_parsed=_jd(), match_result=_match())

    assert result.value is not None
    assert "Redis" in model.prompts[0]
    assert "设计高并发 API" in model.prompts[0]
    assert "缺少 Redis 性能优化案例" in model.prompts[0]


@pytest.mark.core_agent_tests
def test_plan_supports_independent_request_without_jd() -> None:
    model = FakeChatModel([_plan_payload(basis="user_goal")])

    result = build_interview_plan(model, user_goal="准备 Python 后端实习面试")

    assert result.value is not None
    assert result.value.plan[0].basis == "user_goal"
    assert "Independent interview request" in model.prompts[0]


@pytest.mark.core_agent_tests
def test_plan_rejects_a_source_that_is_not_available() -> None:
    model = FakeChatModel([_plan_payload(basis="jd")])

    with pytest.raises(ValueError, match="cannot use jd basis"):
        build_interview_plan(model, user_goal="独立面试")


@pytest.mark.core_agent_tests
def test_initialize_state_preserves_plan_and_requested_count() -> None:
    state = initialize_interview_state(InterviewPlanOutput.model_validate(_plan_payload()), target_question_count=8)

    assert state.status == "planning"
    assert state.target_question_count == 8
    assert [topic.topic_id for topic in state.plan] == ["cache", "project"]


@pytest.mark.core_agent_tests
def test_ask_question_assigns_program_identity_and_uses_plan_topic() -> None:
    model = FakeChatModel([{"topic": "缓存", "question": "请比较互斥锁和逻辑过期。"}])

    result = ask_question(model, _state())

    assert result.value is not None
    assert result.value.question_id == "q-1"
    assert result.value.follow_up_of is None
    assert result.value.scores == {}


@pytest.mark.core_agent_tests
def test_ask_question_rejects_duplicate_or_out_of_plan_question() -> None:
    history = _evaluated_record()
    duplicate_model = FakeChatModel([{"topic": "缓存", "question": "如何处理缓存击穿？"}])
    out_of_plan_model = FakeChatModel([{"topic": "数据库", "question": "如何设计索引？"}])

    with pytest.raises(ValueError, match="must not duplicate"):
        ask_question(duplicate_model, _state([history]))
    with pytest.raises(ValueError, match="must be allowed"):
        ask_question(out_of_plan_model, _state([history]))


@pytest.mark.core_agent_tests
def test_ask_follow_up_keeps_parent_topic_and_reference() -> None:
    parent = _evaluated_record()
    model = FakeChatModel([{"topic": "缓存", "question": "请给出该方案的 QPS 数据。"}])

    result = ask_question(model, _state([parent]), mode="evidence_follow_up", follow_up_of="q-1")

    assert result.value is not None
    assert result.value.question_id == "q-2"
    assert result.value.follow_up_of == "q-1"
    assert result.value.topic == "缓存"


@pytest.mark.core_agent_tests
def test_evaluate_answer_merges_only_evaluation_fields_with_existing_question_and_answer() -> None:
    record = _evaluated_record().model_copy(update={"scores": {}, "feedback": "", "strengths": [], "issues": [], "answer_relevance": None})
    model = FakeChatModel(
        [
            {
                "scores": {"technical_accuracy": 85, "structure": 75, "job_relevance": 80, "evidence": 55},
                "feedback": "概念准确，但缺少量化结果。",
                "strengths": ["说明了互斥锁。"],
                "issues": ["未提供性能数据。"],
                "answer_relevance": "on_topic",
                "fatal_error": False,
                "fatal_error_reason": None,
            }
        ]
    )

    result = evaluate_answer(model, record, user_goal="后端工程师")

    assert result.value is not None
    assert result.value.question_id == record.question_id
    assert result.value.question == record.question
    assert result.value.answer == record.answer
    assert result.value.scores["technical_accuracy"] == 85.0
    assert result.value.issues == ["未提供性能数据。"]


@pytest.mark.core_agent_tests
def test_evaluate_answer_uses_structured_output_retry_without_mutating_record() -> None:
    record = _evaluated_record().model_copy(update={"scores": {}, "feedback": "", "strengths": [], "issues": [], "answer_relevance": None})
    model = FakeChatModel([{"bad": "json"}, {"bad": "json"}, {"bad": "json"}])

    result = evaluate_answer(model, record)

    assert result.value is None
    assert result.degraded is True
    assert result.retry_count == 2
    assert len(result.error_log) == 3
    assert record.scores == {}


@pytest.mark.core_agent_tests
def test_report_uses_deterministic_score_and_rejects_unknown_question_references() -> None:
    state = _state([_evaluated_record()])
    valid_narrative = {
        "performance_summary": "缓存基础较好，但样本不足。",
        "recurring_strengths": ["能说明互斥锁。"],
        "recurring_weaknesses": [],
        "review_actions": [],
        "question_references": ["q-1"],
    }
    model = FakeChatModel([valid_narrative])

    result = generate_review_report(model, state, completion_reason="user_ended", jd_parsed=_jd())

    assert result.value is not None
    assert result.value.overall_score == 75.0
    assert result.value.completion_reason == "user_ended"
    assert result.value.sample_limited is True
    unknown_model = FakeChatModel([{**valid_narrative, "question_references": ["q-404"]}])
    with pytest.raises(ValueError, match="existing question IDs"):
        generate_review_report(unknown_model, state, completion_reason="user_ended")