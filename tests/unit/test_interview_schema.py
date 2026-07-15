"""模拟面试 Schema 与评价门卫层的单元测试。"""

import pytest
from pydantic import ValidationError

from app.schemas.interview import AnswerEvaluation, InterviewReport, InterviewState, InterviewTopicPlan, QuestionRecord


def _scores(**overrides: float) -> dict[str, float]:
    values = {
        "technical_accuracy": 75.0,
        "structure": 70.0,
        "job_relevance": 72.0,
        "evidence": 68.0,
    }
    values.update(overrides)
    return values


def _evaluation(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "scores": _scores(),
        "feedback": "回答给出了结论和关键取舍。",
        "strengths": ["能说明缓存策略。"],
        "issues": [],
        "answer_relevance": "on_topic",
        "fatal_error": False,
        "fatal_error_reason": None,
    }
    payload.update(overrides)
    return payload


@pytest.mark.core_agent_tests
def test_answer_evaluation_accepts_complete_consistent_output() -> None:
    evaluation = AnswerEvaluation.model_validate(_evaluation())

    assert evaluation.scores["technical_accuracy"] == 75.0
    assert evaluation.answer_relevance == "on_topic"


@pytest.mark.core_agent_tests
@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (_evaluation(scores={"technical_accuracy": 80.0}), "exactly the four"),
        (_evaluation(scores=_scores(evidence=101.0)), "within 0..100"),
        (_evaluation(fatal_error=True, fatal_error_reason=None, issues=["概念错误"], scores=_scores(technical_accuracy=30.0)), "fatal_error requires"),
        (_evaluation(fatal_error=True, fatal_error_reason="将缓存击穿和穿透混为一谈", issues=[], scores=_scores(technical_accuracy=30.0)), "fatal_error requires"),
        (_evaluation(fatal_error=True, fatal_error_reason="将缓存击穿和穿透混为一谈", issues=["概念错误"], scores=_scores(technical_accuracy=60.0)), "fatal_error requires"),
        (_evaluation(fatal_error=False, fatal_error_reason="不应存在"), "must be null"),
        (_evaluation(answer_relevance="off_topic", scores=_scores(job_relevance=60.0)), "off_topic requires"),
        (_evaluation(answer_relevance="on_topic", scores=_scores(job_relevance=39.0)), "on_topic requires"),
    ],
)
def test_answer_evaluation_gate_rejects_inconsistent_route_signals(payload: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        AnswerEvaluation.model_validate(payload)


@pytest.mark.core_agent_tests
def test_question_record_allows_empty_scores_while_waiting_for_answer() -> None:
    record = QuestionRecord(
        question_id="q-1",
        topic="项目经历",
        question="请介绍一个项目。",
        answer="",
        follow_up_of=None,
        scores={},
        feedback="",
        strengths=[],
        issues=[],
    )

    assert record.scores == {}
    assert record.answer_relevance is None


@pytest.mark.core_agent_tests
def test_question_record_rejects_partial_scores() -> None:
    with pytest.raises(ValidationError, match="empty or contain exactly"):
        QuestionRecord(
            question_id="q-1",
            topic="项目经历",
            question="请介绍一个项目。",
            answer="回答",
            follow_up_of=None,
            scores={"technical_accuracy": 80.0},
            feedback="反馈",
            strengths=[],
            issues=[],
        )


@pytest.mark.core_agent_tests
def test_question_record_rejects_evaluation_signals_without_scores() -> None:
    with pytest.raises(ValidationError, match="unevaluated records"):
        QuestionRecord(
            question_id="q-1",
            topic="项目经历",
            question="请介绍一个项目。",
            answer="",
            follow_up_of=None,
            scores={},
            feedback="",
            strengths=[],
            issues=[],
            answer_relevance="on_topic",
        )


@pytest.mark.core_agent_tests
def test_interview_report_requires_complete_bounded_dimension_scores() -> None:
    report = InterviewReport(
        overall_score=72.5,
        dimension_scores=_scores(),
        performance_summary="已覆盖核心项目与后端基础主题。",
        recurring_strengths=["能说明工程取舍。"],
        recurring_weaknesses=[],
        review_actions=[],
        question_references=["q-1"],
        completion_reason="target_reached",
        covered_topics=["项目经历"],
        uncovered_topics=[],
        sample_limited=False,
    )

    assert report.completion_reason == "target_reached"
    with pytest.raises(ValidationError, match="within 0..100"):
        InterviewReport.model_validate({**report.model_dump(), "dimension_scores": _scores(evidence=-0.1)})


@pytest.mark.core_agent_tests
def test_interview_state_freezes_plan_and_question_count_range() -> None:
    state = InterviewState(
        status="planning",
        target_question_count=8,
        current_question_id=None,
        question_records=[],
        user_context_updates=[],
        report=None,
        plan=[
            InterviewTopicPlan(
                topic_id="backend-foundation",
                topic="后端基础",
                objective="考察并发与缓存基础。",
                priority="core",
                basis="jd",
            )
        ],
    )

    assert state.plan[0].basis == "jd"
    with pytest.raises(ValidationError, match="less than or equal to 15"):
        InterviewState.model_validate({**state.model_dump(), "target_question_count": 16})