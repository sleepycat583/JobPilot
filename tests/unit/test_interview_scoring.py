"""模拟面试确定性决策、评分与报告完整性元数据测试。"""

import pytest

from app.schemas.interview import AnswerRelevance, InterviewState, InterviewTopicPlan, QuestionRecord
from app.services.interview_scoring import (
    MAX_INTERVIEW_QUESTION_COUNT,
    build_completion_metadata,
    calculate_interview_score,
    count_consecutive_follow_ups,
    decide_next_interview_action,
)


def _plan() -> list[InterviewTopicPlan]:
    return [
        InterviewTopicPlan(topic_id="project", topic="项目经历", objective="考察项目贡献。", priority="core", basis="jd"),
        InterviewTopicPlan(topic_id="foundation", topic="后端基础", objective="考察基础概念。", priority="core", basis="jd"),
        InterviewTopicPlan(topic_id="collaboration", topic="协作沟通", objective="考察团队协作。", priority="supporting", basis="general"),
    ]


def _record(
    question_id: str,
    topic: str,
    *,
    follow_up_of: str | None = None,
    technical: float = 70.0,
    structure: float = 70.0,
    relevance: float = 70.0,
    evidence: float = 70.0,
    answer_relevance: AnswerRelevance = "on_topic",
) -> QuestionRecord:
    return QuestionRecord(
        question_id=question_id,
        topic=topic,
        question="请说明你的方案。",
        answer="我会先分析约束，再给出取舍和验证结果。",
        follow_up_of=follow_up_of,
        scores={
            "technical_accuracy": technical,
            "structure": structure,
            "job_relevance": relevance,
            "evidence": evidence,
        },
        feedback="回答包含了关键步骤。",
        strengths=["结构清晰。"],
        issues=[],
        answer_relevance=answer_relevance,
    )


def _state(records: list[QuestionRecord], *, target: int = 8) -> InterviewState:
    return InterviewState(
        status="evaluating",
        target_question_count=target,
        current_question_id=records[-1].question_id if records else None,
        question_records=records,
        user_context_updates=[],
        report=None,
        plan=_plan(),
    )


@pytest.mark.core_agent_tests
def test_decision_uses_documented_low_technical_follow_up() -> None:
    decision = decide_next_interview_action(_state([_record("q-1", "项目经历", technical=59.9)]))

    assert decision.action == "clarification_follow_up"


@pytest.mark.core_agent_tests
def test_decision_does_not_follow_up_at_technical_threshold() -> None:
    decision = decide_next_interview_action(_state([_record("q-1", "项目经历", technical=60.0)]))

    assert decision.action == "next_topic"


@pytest.mark.core_agent_tests
def test_decision_uses_documented_high_technical_low_evidence_follow_up() -> None:
    decision = decide_next_interview_action(_state([_record("q-1", "项目经历", technical=80.0, evidence=59.9)]))

    assert decision.action == "evidence_follow_up"


@pytest.mark.core_agent_tests
def test_decision_requires_technical_score_of_at_least_eighty_for_evidence_follow_up() -> None:
    decision = decide_next_interview_action(_state([_record("q-1", "项目经历", technical=79.9, evidence=0.0)]))

    assert decision.action == "next_topic"


@pytest.mark.core_agent_tests
def test_decision_gives_one_retry_then_changes_topic_for_off_topic_answer() -> None:
    state = _state([_record("q-1", "项目经历", relevance=59.9, answer_relevance="off_topic")])

    assert decide_next_interview_action(state).action == "retry_same_question"
    assert decide_next_interview_action(state, current_question_retried=True).action == "next_topic"


@pytest.mark.core_agent_tests
def test_decision_limits_same_topic_to_two_consecutive_follow_ups() -> None:
    records = [
        _record("q-1", "项目经历"),
        _record("q-2", "项目经历", follow_up_of="q-1", technical=50.0),
        _record("q-3", "项目经历", follow_up_of="q-2", technical=50.0),
    ]

    assert count_consecutive_follow_ups(records, "项目经历") == 2
    assert decide_next_interview_action(_state(records)).action == "next_topic"


@pytest.mark.core_agent_tests
@pytest.mark.parametrize(
    ("records", "target", "user_ended", "expected_reason"),
    [
        ([_record("q-1", "项目经历")], 8, True, "user_ended"),
        ([_record(f"q-{index}", "协作沟通") for index in range(1, MAX_INTERVIEW_QUESTION_COUNT + 1)], 15, False, "max_questions_reached"),
        ([_record("q-1", "项目经历"), _record("q-2", "后端基础")], 8, False, "topics_completed"),
        ([_record("q-1", "协作沟通")], 1, False, "target_reached"),
    ],
)
def test_decision_uses_ordered_finish_conditions(
    records: list[QuestionRecord], target: int, user_ended: bool, expected_reason: str
) -> None:
    decision = decide_next_interview_action(_state(records, target=target), user_ended=user_ended)

    assert decision.action == "finish"
    assert decision.completion_reason == expected_reason


@pytest.mark.core_agent_tests
def test_decision_at_fifteen_returns_finish_not_another_question_action() -> None:
    records = [_record(f"q-{index}", "协作沟通") for index in range(1, 16)]

    decision = decide_next_interview_action(_state(records, target=15))
    assert decision.completion_reason == "max_questions_reached"
    metadata = build_completion_metadata(_plan(), records, decision.completion_reason)

    assert decision.action == "finish"
    assert decision.action not in {"retry_same_question", "clarification_follow_up", "evidence_follow_up", "next_topic"}
    assert metadata.uncovered_topics == ["项目经历", "后端基础"]
    assert metadata.sample_limited is True


@pytest.mark.core_agent_tests
def test_decision_allows_the_fifteenth_question_before_the_hard_limit() -> None:
    records = [_record(f"q-{index}", "协作沟通") for index in range(1, 15)]

    decision = decide_next_interview_action(_state(records, target=15))

    assert decision.action == "next_topic"
    assert decision.completion_reason is None


@pytest.mark.core_agent_tests
def test_score_weights_primary_question_more_than_follow_up() -> None:
    records = [
        _record("q-1", "项目经历", technical=100.0, structure=100.0, relevance=100.0, evidence=100.0),
        _record(
            "q-2",
            "项目经历",
            follow_up_of="q-1",
            technical=0.0,
            structure=0.0,
            relevance=0.0,
            evidence=0.0,
            answer_relevance="partial",
        ),
    ]

    breakdown = calculate_interview_score(records)

    assert breakdown.dimension_scores == {
        "technical_accuracy": 66.7,
        "structure": 66.7,
        "job_relevance": 66.7,
        "evidence": 66.7,
    }
    assert breakdown.overall_score == 66.7


@pytest.mark.core_agent_tests
def test_score_uses_fixed_dimension_weights() -> None:
    breakdown = calculate_interview_score(
        [_record("q-1", "项目经历", technical=100.0, structure=0.0, relevance=100.0, evidence=0.0)]
    )

    assert breakdown.overall_score == 65.0


@pytest.mark.core_agent_tests
def test_score_returns_zeroes_for_an_interview_ended_before_any_answer() -> None:
    breakdown = calculate_interview_score([])

    assert breakdown.overall_score == 0.0
    assert set(breakdown.dimension_scores.values()) == {0.0}


@pytest.mark.core_agent_tests
def test_score_rejects_unevaluated_record() -> None:
    waiting_record = QuestionRecord(
        question_id="q-1",
        topic="项目经历",
        question="请介绍项目。",
        answer="",
        follow_up_of=None,
        scores={},
        feedback="",
        strengths=[],
        issues=[],
    )

    with pytest.raises(ValueError, match="requires evaluated"):
        calculate_interview_score([waiting_record])


@pytest.mark.core_agent_tests
def test_score_excludes_unavailable_records_but_keeps_available_dimension_means() -> None:
    unavailable = QuestionRecord(
        question_id="q-2",
        topic="项目经历",
        question="请介绍项目。",
        answer="回答已提交。",
        follow_up_of=None,
        scores={},
        feedback="",
        strengths=[],
        issues=[],
        evaluation_status="unavailable",
    )

    breakdown = calculate_interview_score([_record("q-1", "项目经历", technical=80.0), unavailable])

    assert breakdown.dimension_scores is not None
    assert breakdown.dimension_scores["technical_accuracy"] == 80.0
    assert breakdown.overall_score == 74.0


@pytest.mark.core_agent_tests
def test_score_returns_null_only_when_every_answered_record_is_unavailable() -> None:
    unavailable = QuestionRecord(
        question_id="q-1",
        topic="项目经历",
        question="请介绍项目。",
        answer="回答已提交。",
        follow_up_of=None,
        scores={},
        feedback="",
        strengths=[],
        issues=[],
        evaluation_status="unavailable",
    )

    breakdown = calculate_interview_score([unavailable])

    assert breakdown.overall_score is None
    assert breakdown.dimension_scores is None
@pytest.mark.core_agent_tests
def test_completion_metadata_marks_user_ended_partial_interview_as_limited() -> None:
    metadata = build_completion_metadata(_plan(), [_record("q-1", "项目经历")], "user_ended")

    assert metadata.covered_topics == ["项目经历"]
    assert metadata.uncovered_topics == ["后端基础", "协作沟通"]
    assert metadata.sample_limited is True


@pytest.mark.core_agent_tests
def test_completion_metadata_has_no_uncovered_topics_when_all_planned_topics_are_covered() -> None:
    records = [
        _record("q-1", "项目经历"),
        _record("q-2", "后端基础"),
        _record("q-3", "协作沟通"),
    ]

    metadata = build_completion_metadata(_plan(), records, "topics_completed")

    assert metadata.covered_topics == ["项目经历", "后端基础", "协作沟通"]
    assert metadata.uncovered_topics == []
    assert metadata.sample_limited is False