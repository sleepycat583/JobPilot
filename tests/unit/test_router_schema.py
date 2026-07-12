"""RouterDecision Schema 测试。"""

import pytest
from pydantic import ValidationError

from app.schemas.router import RouterDecision


@pytest.mark.core_agent_tests
def test_router_schema_rejects_invalid_route() -> None:
    with pytest.raises(ValidationError):
        RouterDecision.model_validate(
            {
                "route": "unknown",
                "confidence": 0.8,
                "reason": "ok",
                "task_queue": [],
            }
        )


@pytest.mark.core_agent_tests
def test_router_schema_rejects_confidence_out_of_range() -> None:
    with pytest.raises(ValidationError):
        RouterDecision.model_validate(
            {
                "route": "jd_parse",
                "confidence": 1.1,
                "reason": "ok",
                "task_queue": [],
            }
        )


@pytest.mark.core_agent_tests
def test_router_schema_rejects_overlong_reason() -> None:
    with pytest.raises(ValidationError):
        RouterDecision.model_validate(
            {
                "route": "jd_parse",
                "confidence": 0.8,
                "reason": "x" * 201,
                "task_queue": [],
            }
        )


@pytest.mark.core_agent_tests
def test_router_schema_rejects_invalid_task_queue_item() -> None:
    with pytest.raises(ValidationError):
        RouterDecision.model_validate(
            {
                "route": "jd_parse",
                "confidence": 0.8,
                "reason": "ok",
                "task_queue": ["jd_parse", "invalid"],
            }
        )
