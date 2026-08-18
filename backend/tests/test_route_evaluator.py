from scripts.evaluate_supervisor_routes import meets_thresholds, summarize_routes


def test_route_evaluator_reports_exact_and_per_worker_accuracy() -> None:
    cases = [
        {"id": "resume-ok", "expected_worker": "resume_worker"},
        {"id": "resume-miss", "expected_worker": "resume_worker"},
        {"id": "chat-ok", "expected_worker": "chat_worker"},
    ]
    summary = summarize_routes(cases, ["resume_worker", "chat_worker", "chat_worker"])

    assert summary == {
        "passed": 2,
        "failed": 1,
        "total": 3,
        "accuracy": 2 / 3,
        "invalid_tool_calls": 0,
        "worker_accuracy": {"chat_worker": 1.0, "resume_worker": 0.5},
    }
    assert not meets_thresholds(summary, min_overall_accuracy=0.6, min_worker_accuracy=0.8)


def test_route_evaluator_rejects_invalid_or_incomplete_results() -> None:
    cases = [{"id": "resume-invalid", "expected_worker": "resume_worker"}]
    summary = summarize_routes(cases, [None])

    assert summary["invalid_tool_calls"] == 1
    assert not meets_thresholds(summary, min_overall_accuracy=0.0, min_worker_accuracy=0.0)
