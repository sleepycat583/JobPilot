"""Resume Schema 测试。"""

import pytest
from pydantic import ValidationError

from app.schemas.resume import EvidenceRef, MatchItem, MatchResult, MatchUnavailableResult


@pytest.mark.core_agent_tests
def test_evidence_ref_rejects_relevance_out_of_range() -> None:
    with pytest.raises(ValidationError):
        EvidenceRef.model_validate({"chunk_id": "c1", "quote": "q", "relevance": 1.1})


@pytest.mark.core_agent_tests
def test_match_result_rejects_total_score_out_of_range() -> None:
    for invalid_score in (-0.1, 100.1):
        with pytest.raises(ValidationError):
            MatchResult.model_validate(
                {
                    "total_score": invalid_score,
                    "dimension_scores": {},
                    "matched_items": [],
                    "strengths": [],
                    "gaps": [],
                    "recommendations": [],
                    "low_score_review_required": False,
                    "resume_version": "v1",
                }
            )


@pytest.mark.core_agent_tests
def test_match_result_accepts_unbounded_match_item_score() -> None:
    item = MatchItem.model_validate(
        {
            "requirement": "Python",
            "status": "matched",
            "score": 999.0,
            "evidence": [{"chunk_id": "c1", "quote": "q", "relevance": 0.9}],
            "rationale": "ok",
        }
    )

    assert item.score == 999.0


@pytest.mark.core_agent_tests
def test_match_unavailable_result_keeps_evidence_without_score_fields() -> None:
    result = MatchUnavailableResult.model_validate(
        {
            "status": "MATCH_UNAVAILABLE",
            "resume_version": "v1",
            "retrieval_evidence": [{"requirement": "Python", "evidence": [{"chunk_id": "c1", "quote": "Python 项目", "relevance": 0.9}]}],
            "message": "请人工核可",
        }
    )

    assert result.retrieval_evidence[0].evidence[0].chunk_id == "c1"
    assert "total_score" not in result.model_dump()
