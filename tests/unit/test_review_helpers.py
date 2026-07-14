"""Review 确定性辅助函数测试。"""

import pytest

from app.graph.review_helpers import next_match_business_attempt


@pytest.mark.core_agent_tests
def test_next_match_business_attempt_counts_only_successful_matcher_runs() -> None:
    history = [
        {"node": "resume_matcher", "event": "error", "timestamp": "t1", "detail": "failed"},
        {"node": "jd_parser", "event": "success", "timestamp": "t2", "detail": "parsed"},
        {"node": "resume_matcher", "event": "success", "timestamp": "t3", "detail": "match_completed"},
        {"node": "resume_matcher", "event": "success", "timestamp": "t4", "detail": "match_completed"},
    ]

    assert next_match_business_attempt(history) == 3