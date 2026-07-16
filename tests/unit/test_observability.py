"""结构化日志基础设施测试。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from time import sleep

import pytest
from langgraph.errors import GraphInterrupt

from app.services.observability import (
    LOG_FILE_NAME,
    LOG_RETENTION_DAYS,
    SafeTimedRotatingFileHandler,
    build_log_event,
    build_safe_input_summary,
    configure_structured_logger,
    new_node_run_id,
    observe_node,
    redact_text,
)


def test_logger_writes_parseable_jsonl_with_stable_fields(tmp_path: Path) -> None:
    logger = configure_structured_logger(log_dir=tmp_path, logger_name="test.observability.jsonl", include_stdout=False)
    event = build_log_event(
        event="agent_node_finished",
        session_id="session-1",
        thread_id="thread-1",
        node="supervisor",
        node_kind="agent",
        node_run_id="run-1",
        started_at="2026-01-01T00:00:00+00:00",
        ended_at="2026-01-01T00:00:01+00:00",
        duration_ms=1000,
        input_summary="node=supervisor; user_input_length=12",
        success=True,
    )

    logger.info(event)
    _flush(logger)
    payload = json.loads((tmp_path / LOG_FILE_NAME).read_text(encoding="utf-8").strip())

    assert payload["level"] == "INFO"
    assert {"timestamp", "event", "event_id", "session_id", "thread_id", "node", "node_kind", "node_run_id", "success"} <= payload.keys()
    assert payload["event"] == "agent_node_finished"
    assert payload["duration_ms"] == 1000


def test_logger_uses_daily_rotation_and_fourteen_backups(tmp_path: Path) -> None:
    logger = configure_structured_logger(log_dir=tmp_path, logger_name="test.observability.rotation", include_stdout=False)

    handler = next(handler for handler in logger.handlers if isinstance(handler, SafeTimedRotatingFileHandler))

    assert handler.when == "MIDNIGHT"
    assert handler.backupCount == LOG_RETENTION_DAYS


def test_event_ids_are_unique_and_node_run_id_links_lifecycle() -> None:
    node_run_id = new_node_run_id()
    started = build_log_event(event="agent_node_started", session_id="s", thread_id="t", node_run_id=node_run_id)
    finished = build_log_event(event="agent_node_finished", session_id="s", thread_id="t", node_run_id=node_run_id)

    assert started["event_id"] != finished["event_id"]
    assert started["node_run_id"] == finished["node_run_id"] == node_run_id


@pytest.mark.parametrize(
    "raw, forbidden",
    [
        ("邮箱 user@example.com，手机号 13800138000", ["user@example.com", "13800138000"]),
        ("api_key=sk-secret-value; Bearer token=abc", ["sk-secret-value", "abc"]),
        ("Authorization: Bearer sk-header-secret", ["sk-header-secret"]),
        ("password=hunter2; secret=internal-value", ["hunter2", "internal-value"]),
    ],
)
def test_redact_text_removes_sensitive_values(raw: str, forbidden: list[str]) -> None:
    redacted = redact_text(raw)

    assert all(value not in redacted for value in forbidden)
    assert "[REDACTED" in redacted


def test_redact_text_limits_long_user_content() -> None:
    redacted = redact_text("x" * 501)

    assert redacted.endswith("...[TRUNCATED]")
    assert len(redacted) > 500


def test_safe_input_summary_contains_only_metadata() -> None:
    summary = build_safe_input_summary(
        {"user_input": "完整简历和JD内容不应出现", "resume_version": "resume-v1", "jd_parsed": None, "match_result": None},
        "resume_matcher",
    )

    assert "完整简历" not in summary
    assert "user_input_length=13" in summary
    assert "resume_version=resume-v1" in summary


def test_observer_double_writes_same_returned_error_entry_to_jsonl(tmp_path: Path) -> None:
    logger = configure_structured_logger(log_dir=tmp_path, logger_name="test.observability.double-write", include_stdout=False)
    entry = {"code": "INPUT_EMPTY", "node": "supervisor", "message": "empty", "retryable": False, "attempt": 0, "timestamp": "2026-01-01T00:00:00+00:00", "raw_output_excerpt": None}
    wrapped = observe_node("supervisor", "agent", lambda _: {"error_log": [entry]}, logger)

    update = wrapped({"user_input": ""}, {"configurable": {"session_id": "s", "thread_id": "t"}})
    _flush(logger)
    events = [json.loads(line) for line in (tmp_path / LOG_FILE_NAME).read_text(encoding="utf-8").splitlines()]
    failed = next(event for event in events if event["event"] == "agent_node_failed")

    assert update["error_log"][0] is entry
    assert failed["error_entry"] == entry
    assert {key: failed[key] for key in ("error_code", "attempt", "message", "node")} == {"error_code": entry["code"], "attempt": entry["attempt"], "message": entry["message"], "node": entry["node"]}


def test_observer_normalizes_sensitive_error_entry_before_both_writes(tmp_path: Path) -> None:
    logger = configure_structured_logger(log_dir=tmp_path, logger_name="test.observability.error-redaction", include_stdout=False)
    entry = {"code": "LLM_SCHEMA_INVALID", "node": "jd_parser", "message": "Authorization: Bearer sk-sensitive", "retryable": True, "attempt": 1, "timestamp": "2026-01-01T00:00:00+00:00", "raw_output_excerpt": "邮箱 user@example.com"}
    update = observe_node("jd_parser", "agent", lambda _: {"error_log": [entry]}, logger)({}, {"configurable": {"session_id": "s", "thread_id": "t"}})
    _flush(logger)
    failed = next(json.loads(line) for line in (tmp_path / LOG_FILE_NAME).read_text(encoding="utf-8").splitlines() if 'agent_node_failed' in line)

    assert "sk-sensitive" not in update["error_log"][0]["message"]
    assert "user@example.com" not in update["error_log"][0]["raw_output_excerpt"]
    assert failed["error_entry"] is not entry  # JSON round-trip creates a new Python object.
    assert failed["error_entry"] == update["error_log"][0]


def test_observer_persists_unhandled_exception_after_120ms(tmp_path: Path) -> None:
    logger = configure_structured_logger(log_dir=tmp_path, logger_name="test.observability.unhandled", include_stdout=False)

    def raise_unhandled(_: object) -> dict[str, object]:
        sleep(0.12)
        raise RuntimeError("unhandled test failure")

    update = observe_node("jd_parser", "agent", raise_unhandled, logger)({}, {"configurable": {"session_id": "s", "thread_id": "t"}})
    _flush(logger)
    events = [json.loads(line) for line in (tmp_path / LOG_FILE_NAME).read_text(encoding="utf-8").splitlines()]
    failed = next(event for event in events if event["event"] == "agent_node_failed")

    assert update["error_log"][0]["code"] == "UNHANDLED_NODE_EXCEPTION"
    assert update["execution_history"][0]["event"] == "error"
    assert failed["error_code"] == update["error_log"][0]["code"]
    assert failed["duration_ms"] >= 120


def test_observer_preserves_graph_interrupt_without_error_entry(tmp_path: Path) -> None:
    logger = configure_structured_logger(log_dir=tmp_path, logger_name="test.observability.interrupt", include_stdout=False)

    def interrupt(_: object) -> dict[str, object]:
        raise GraphInterrupt()

    with pytest.raises(GraphInterrupt):
        observe_node("interview_await_answer", "control", interrupt, logger)({}, {"configurable": {"session_id": "s", "thread_id": "t"}})
    _flush(logger)
    events = [json.loads(line) for line in (tmp_path / LOG_FILE_NAME).read_text(encoding="utf-8").splitlines()]

    assert [event["event"] for event in events] == ["agent_node_started", "agent_node_interrupted"]


def _flush(logger: logging.Logger) -> None:
    for handler in logger.handlers:
        handler.flush()