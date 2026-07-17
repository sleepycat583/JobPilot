"""State Schema 与 reducer 测试。"""

from operator import add
from typing import get_type_hints

import pytest

from app.schemas.resume import MatchUnavailableResult
from app.schemas.state import ErrorEntry, ExecutionEvent, JobAssistantState


@pytest.mark.core_agent_tests
def test_state_reducer_annotations_use_operator_add() -> None:
    hints = get_type_hints(JobAssistantState, include_extras=True)

    execution_meta = hints["execution_history"].__metadata__
    error_meta = hints["error_log"].__metadata__

    assert execution_meta == (add,)
    assert error_meta == (add,)


@pytest.mark.core_agent_tests
def test_operator_add_appends_state_lists() -> None:
    first_event: ExecutionEvent = {
        "node": "router",
        "event": "enter",
        "timestamp": "t1",
        "detail": "d1",
    }
    second_event: ExecutionEvent = {
        "node": "jd_agent",
        "event": "success",
        "timestamp": "t2",
        "detail": "d2",
    }
    first_error: ErrorEntry = {
        "code": "E1",
        "node": "router",
        "message": "m1",
        "retryable": True,
        "attempt": 0,
        "timestamp": "t1",
        "raw_output_excerpt": None,
    }
    second_error: ErrorEntry = {
        "code": "E2",
        "node": "jd_agent",
        "message": "m2",
        "retryable": False,
        "attempt": 1,
        "timestamp": "t2",
        "raw_output_excerpt": "x",
    }

    assert add([first_event], [second_event]) == [first_event, second_event]
    assert add([first_error], [second_error]) == [first_error, second_error]


@pytest.mark.core_agent_tests
def test_execution_event_metadata_is_optional_and_structured() -> None:
    event: ExecutionEvent = {
        "node": "resume_matcher",
        "event": "success",
        "timestamp": "t1",
        "detail": "match_completed",
        "metadata": {"business_attempt": 2, "resume_version": "v2", "total_score": 72.0},
    }

    assert event["metadata"]["business_attempt"] == 2


@pytest.mark.core_agent_tests
def test_state_can_hold_independent_business_fields() -> None:
    state: JobAssistantState = {
        "jd_parsed": None,
        "match_result": None,
        "interview_state": None,
    }

    assert "jd_parsed" in state
    assert "match_result" in state
    assert "interview_state" in state


@pytest.mark.core_agent_tests
def test_state_match_result_annotation_accepts_unavailable_result() -> None:
    result = MatchUnavailableResult(
        status="MATCH_UNAVAILABLE", resume_version="v1", retrieval_evidence=[], message="请人工核可"
    )
    state: JobAssistantState = {"match_result": result}

    assert state["match_result"].status == "MATCH_UNAVAILABLE"
