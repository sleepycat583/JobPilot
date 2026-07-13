"""SQLite Checkpoint 跨进程恢复验收。"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


CHILD_PROGRAM = r'''
import json
import sqlite3
import sys
from langgraph.graph import END, StateGraph
from langgraph.types import Command
from app.graph.checkpoint import open_sqlite_checkpointer
from app.graph.control_nodes import finalize_node, low_score_cancelled_node, low_score_gate_node, prepare_low_score_review_node
from app.schemas.resume import MatchResult
from app.schemas.state import JobAssistantState

path, thread_id, phase = sys.argv[1:]
checkpointer, connection = open_sqlite_checkpointer(path)
graph = StateGraph(JobAssistantState)
graph.add_node("prepare_low_score_review", prepare_low_score_review_node)
graph.add_node("low_score_gate", low_score_gate_node)
graph.add_node("finalize_node", finalize_node)
graph.add_node("low_score_cancelled", low_score_cancelled_node)
graph.set_entry_point("prepare_low_score_review")
graph.add_edge("prepare_low_score_review", "low_score_gate")
graph.add_conditional_edges("low_score_gate", lambda state: "cancel" if state.get("review_status") == "rejected" else "continue", {"continue": "finalize_node", "cancel": "low_score_cancelled"})
graph.add_edge("finalize_node", END)
graph.add_edge("low_score_cancelled", END)
compiled = graph.compile(checkpointer=checkpointer)
config = {"configurable": {"thread_id": thread_id}}
if phase == "interrupt":
    result = compiled.invoke({
        "thread_id": thread_id,
        "jd_parsed": {"job_title": "Java", "seniority": "mid", "company_name": None, "responsibilities": ["API"], "skills": [], "experience_requirements": [], "education_requirements": [], "interview_focus": [], "company_context": [], "ambiguities": [], "source_language": "zh-CN"},
        "match_result": MatchResult(total_score=59.9, dimension_scores={"must": 20.0}, matched_items=[], strengths=["Java"], gaps=["Kubernetes"], recommendations=["补齐"], low_score_review_required=True, resume_version="resume-v1"),
        "error_log": [{"code": "RAG_EMPTY_RESULT", "node": "resume_matcher", "message": "none", "retryable": False, "attempt": 0, "timestamp": "2026-07-13T00:00:00+00:00", "raw_output_excerpt": None}],
        "execution_history": [{"node": "resume_matcher", "event": "success", "timestamp": "2026-07-13T00:00:00+00:00", "detail": "matched"}],
        "retry_count": {"resume_matcher": 1},
        "conversation_summary": "已完成匹配",
        "summarized_message_count": 6,
        "final_output": None,
    }, config=config)
    snapshot = compiled.get_state(config)
    print(json.dumps({"state": snapshot.values, "interrupt": snapshot.tasks[0].interrupts[0].value}, default=lambda value: value.model_dump() if hasattr(value, "model_dump") else str(value)))
else:
    action = "continue" if phase == "resume_continue" else "cancel"
    result = compiled.invoke(Command(resume={"action": action, "feedback": "用户决定"}), config=config)
    print(json.dumps(result, default=lambda value: value.model_dump() if hasattr(value, "model_dump") else str(value)))
connection.close()
'''


def _run_child(checkpoint_path: Path, thread_id: str, phase: str) -> dict[str, object]:
    """在新解释器中创建或恢复 Graph，证明结果不依赖父进程内存。"""

    completed = subprocess.run(
        [sys.executable, "-c", CHILD_PROGRAM, str(checkpoint_path), thread_id, phase],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout)


@pytest.mark.core_agent_tests
@pytest.mark.parametrize(
    ("resume_phase", "expected_status", "expected_node"),
    [
        ("resume_continue", "approved", "finalize_node"),
        ("resume_cancel", "rejected", "low_score_cancelled"),
    ],
)
def test_sqlite_checkpoint_recovers_complete_state_in_fresh_process(
    tmp_path: Path,
    resume_phase: str,
    expected_status: str,
    expected_node: str,
) -> None:
    checkpoint_path = tmp_path / "checkpoints.sqlite3"
    thread_id = "restart-proof-thread"

    interrupted = _run_child(checkpoint_path, thread_id, "interrupt")
    baseline = interrupted["state"]
    assert interrupted["interrupt"]["type"] == "low_match_score"
    assert baseline["review_status"] == "in_review"

    resumed = _run_child(checkpoint_path, thread_id, resume_phase)
    assert resumed["review_status"] == expected_status
    assert resumed["current_node"] == expected_node
    for field in ("thread_id", "jd_parsed", "match_result", "error_log", "retry_count", "conversation_summary", "summarized_message_count"):
        assert resumed[field] == baseline[field]
    assert resumed["execution_history"][: len(baseline["execution_history"])] == baseline["execution_history"]
    assert resumed.get("final_output") is None

    connection = sqlite3.connect(checkpoint_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] > 0
    finally:
        connection.close()