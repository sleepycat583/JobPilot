"""Review HITL Schema 测试。"""

import pytest
from pydantic import TypeAdapter, ValidationError

from app.schemas.review import HITLCommand, HITLInterruptPayload, LowScoreInterruptPayload, LowScoreReviewCommand


@pytest.mark.core_agent_tests
def test_low_score_interrupt_payload_accepts_minimal_review_context() -> None:
    payload = LowScoreInterruptPayload(
        type="low_match_score",
        score=59.9,
        threshold=60.0,
        top_gaps=["Kubernetes"],
        accepted_actions=["continue", "revise_inputs", "cancel"],
    )

    assert payload.model_dump()["score"] == 59.9


@pytest.mark.core_agent_tests
@pytest.mark.parametrize(
    "invalid_payload",
    [
        {"type": "unexpected", "score": 59.9, "threshold": 60.0, "top_gaps": [], "accepted_actions": ["continue"]},
        {"type": "low_match_score", "score": 100.1, "threshold": 60.0, "top_gaps": [], "accepted_actions": ["continue"]},
        {"type": "low_match_score", "score": 59.9, "threshold": 60.0, "top_gaps": ["a"] * 6, "accepted_actions": ["continue"]},
        {"type": "low_match_score", "score": 59.9, "threshold": 60.0, "top_gaps": [], "accepted_actions": ["approve"]},
    ],
)
def test_low_score_interrupt_payload_rejects_invalid_contract(invalid_payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        LowScoreInterruptPayload.model_validate(invalid_payload)


@pytest.mark.core_agent_tests
def test_low_score_review_command_validates_revise_inputs() -> None:
    with pytest.raises(ValidationError):
        LowScoreReviewCommand.model_validate({"action": "revise_inputs"})


@pytest.mark.core_agent_tests
def test_hitl_command_union_rejects_cross_gate_fields() -> None:
    adapter = TypeAdapter(HITLCommand)

    command = adapter.validate_python({"type": "final_review", "action": "approve"})
    assert command.type == "final_review"

    with pytest.raises(ValidationError):
        adapter.validate_python({"type": "final_review", "action": "continue"})


@pytest.mark.core_agent_tests
def test_hitl_payload_union_selects_interview_contract() -> None:
    adapter = TypeAdapter(HITLInterruptPayload)

    payload = adapter.validate_python(
        {
            "type": "interview_answer",
            "question_id": "q-1",
            "question": "Explain cache invalidation.",
            "accepted_actions": ["submit_answer", "context_update", "end_interview"],
        }
    )

    assert payload.target == "interview_state"