"""Review HITL Schema 测试。"""

import pytest
from pydantic import ValidationError

from app.schemas.review import LowScoreInterruptPayload, LowScoreReviewCommand


@pytest.mark.core_agent_tests
def test_low_score_interrupt_payload_accepts_minimal_review_context() -> None:
    payload = LowScoreInterruptPayload(
        type="low_match_score",
        score=59.9,
        threshold=60.0,
        top_gaps=["Kubernetes"],
        accepted_actions=["continue", "cancel"],
    )

    assert payload.model_dump()["score"] == 59.9


@pytest.mark.core_agent_tests
@pytest.mark.parametrize(
    "invalid_payload",
    [
        {"type": "unexpected", "score": 59.9, "threshold": 60.0, "top_gaps": [], "accepted_actions": ["continue"]},
        {"type": "low_match_score", "score": 100.1, "threshold": 60.0, "top_gaps": [], "accepted_actions": ["continue"]},
        {"type": "low_match_score", "score": 59.9, "threshold": 60.0, "top_gaps": ["a"] * 6, "accepted_actions": ["continue"]},
        {"type": "low_match_score", "score": 59.9, "threshold": 60.0, "top_gaps": [], "accepted_actions": ["revise_inputs"]},
    ],
)
def test_low_score_interrupt_payload_rejects_invalid_contract(invalid_payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        LowScoreInterruptPayload.model_validate(invalid_payload)


@pytest.mark.core_agent_tests
def test_low_score_review_command_excludes_deferred_revise_inputs() -> None:
    with pytest.raises(ValidationError):
        LowScoreReviewCommand.model_validate({"action": "revise_inputs"})