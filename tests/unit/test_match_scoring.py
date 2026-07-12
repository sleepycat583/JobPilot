"""确定性匹配评分服务测试。"""

from app.services.match_scoring import ScoreableMatchItem, calculate_match_score


def test_match_scoring_triggers_low_score_review_for_59_9() -> None:
    breakdown = calculate_match_score(
        must_items=[ScoreableMatchItem("Java", "transferable", 1, 1.0, recent=True, quantified=True)],
        responsibility_items=[
            ScoreableMatchItem("API design", "transferable", 1, 0.4, recent=True, quantified=True)
        ],
        preferred_items=[],
        constraint_statuses=["satisfied"],
    )

    assert breakdown.total_score == 59.9
    assert breakdown.low_score_review_required is True


def test_match_scoring_does_not_trigger_low_score_review_for_60_0() -> None:
    breakdown = calculate_match_score(
        must_items=[ScoreableMatchItem("Java", "transferable", 1, 1.0, recent=True, quantified=True)],
        responsibility_items=[
            ScoreableMatchItem("API design", "transferable", 1, 0.4286, recent=True, quantified=True)
        ],
        preferred_items=[],
        constraint_statuses=["satisfied"],
    )

    assert breakdown.total_score == 60.0
    assert breakdown.low_score_review_required is False


def test_match_scoring_caps_empty_rag_result_to_ten_points() -> None:
    breakdown = calculate_match_score(
        must_items=[ScoreableMatchItem("Java", "matched", 0, 0.0)],
        responsibility_items=[ScoreableMatchItem("API design", "matched", 0, 0.0)],
        preferred_items=[ScoreableMatchItem("Kubernetes", "matched", 0, 0.0)],
        constraint_statuses=["satisfied", "satisfied"],
    )

    assert breakdown.rag_empty_result is True
    assert breakdown.total_score == 10.0


def test_match_scoring_evidence_quality_uses_70_15_15_formula() -> None:
    breakdown = calculate_match_score(
        must_items=[
            ScoreableMatchItem("Java", "matched", 1, 0.8, recent=False, quantified=True),
            ScoreableMatchItem("Spring Boot", "matched", 1, 1.0, recent=True, quantified=True),
            ScoreableMatchItem("Kubernetes", "matched", 0, 0.0, recent=True, quantified=True),
        ],
        responsibility_items=[],
        preferred_items=[],
        constraint_statuses=[],
    )

    assert breakdown.dimension_scores["evidence_quality_score"] == 85.5