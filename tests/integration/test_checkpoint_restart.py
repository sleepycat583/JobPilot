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
from app.graph.control_nodes import final_review_gate_node, finalize_node, low_score_cancelled_node, low_score_gate_node, prepare_final_review_node, prepare_low_score_review_node
from app.schemas.resume import MatchResult
from app.schemas.state import JobAssistantState

path, thread_id, phase = sys.argv[1:]
checkpointer, connection = open_sqlite_checkpointer(path)
graph = StateGraph(JobAssistantState)
graph.add_node("prepare_low_score_review", prepare_low_score_review_node)
graph.add_node("low_score_gate", low_score_gate_node)
graph.add_node("prepare_final_review", prepare_final_review_node)
graph.add_node("final_review_gate", final_review_gate_node)
graph.add_node("finalize_node", finalize_node)
graph.add_node("low_score_cancelled", low_score_cancelled_node)
graph.set_entry_point("prepare_low_score_review")
graph.add_edge("prepare_low_score_review", "low_score_gate")
graph.add_conditional_edges("low_score_gate", lambda state: "cancel" if state.get("review_status") == "rejected" else "continue", {"continue": "prepare_final_review", "cancel": "low_score_cancelled"})
graph.add_edge("prepare_final_review", "final_review_gate")
graph.add_conditional_edges("final_review_gate", lambda state: "reject" if state.get("review_status") == "rejected" else "approve", {"approve": "finalize_node", "reject": "low_score_cancelled"})
graph.add_edge("finalize_node", END)
graph.add_edge("low_score_cancelled", END)
compiled = graph.compile(checkpointer=checkpointer)
config = {"configurable": {"thread_id": thread_id}}
if phase == "interrupt":
    result = compiled.invoke({
        "thread_id": thread_id,
        "jd_parsed": {"job_title": "Java", "seniority": "mid", "company_name": None, "responsibilities": ["API"], "skills": [], "experience_requirements": [], "education_requirements": [], "interview_focus": [], "company_context": [], "ambiguities": [], "source_language": "zh-CN"},
        "match_result": MatchResult(total_score=59.9, dimension_scores={"must": 20.0}, matched_items=[], strengths=["Java"], gaps=["Kubernetes"], recommendations=["补齐"], low_score_review_required=True, resume_version="resume-v1"),
        "review_target": "match_result",
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
    snapshot = compiled.get_state(config)
    print(json.dumps({"state": result, "interrupt": snapshot.tasks[0].interrupts[0].value if snapshot.tasks and snapshot.tasks[0].interrupts else None}, default=lambda value: value.model_dump() if hasattr(value, "model_dump") else str(value)))
connection.close()
'''

COMBINED_CHILD_PROGRAM = r'''
import json
import sys
from dataclasses import dataclass
from typing import Any
from langgraph.types import Command
from app.graph.builder import build_graph
from app.graph.checkpoint import open_sqlite_checkpointer

JD_TEXT = "后端工程师岗位，要求熟悉 Java、Spring Boot，并具备三年以上接口设计经验。"
JD_JSON = '{"job_title":"Java后端工程师","seniority":"mid","company_name":null,"responsibilities":["API design"],"skills":[{"name":"Java","category":"language","priority":"must","evidence":"熟悉 Java"}],"experience_requirements":["3年以上后端开发经验"],"education_requirements":[],"interview_focus":[],"company_context":[],"ambiguities":[],"source_language":"zh-CN"}'
MATCH_JSON = '{"must_items":[{"requirement":"Java","status":"transferable","rationale":"ok","evidence":[{"chunk_id":"java-1","quote":"Java"}],"recent":true,"quantified":true}],"responsibility_items":[{"requirement":"API design","status":"transferable","rationale":"ok","evidence":[{"chunk_id":"api-1","quote":"API design"}],"recent":true,"quantified":true}],"preferred_items":[],"constraint_items":[{"requirement":"3年以上后端开发经验","status":"satisfied","rationale":"ok","evidence":[{"chunk_id":"exp-1","quote":"3 years"}]}],"strengths":["strong"],"gaps":[],"recommendations":[]}'
HIGH_MATCH_JSON = MATCH_JSON.replace('"quote":"API design"', '"quote":"API design 1.0"')

@dataclass
class Model:
    calls: int = 0
    revise_mode: bool = False
    def invoke(self, prompt: str) -> str:
        responses = ['{"route":"jd_parse","confidence":0.9,"reason":"combo","task_queue":["jd_parse","resume_match"]}', JD_JSON, MATCH_JSON, HIGH_MATCH_JSON]
        index = 3 if self.revise_mode else self.calls
        value = responses[min(index, len(responses) - 1)]
        self.calls += 1
        return value
    def bind(self, **_: Any): return self

class Store:
    calls = 0
    def query(self, text: str, version: str):
        self.calls += 1
        relevance = 0.4 if text == "API design" else 1.0
        chunk_id = {"Java": "java-1", "API design": "api-1", "3年以上后端开发经验": "exp-1"}[text]
        quote = "API design 1.0" if text == "API design" and version == "resume-v2" else text
        return [{"chunk_id": chunk_id, "quote": quote, "relevance": relevance if version != "resume-v2" else 1.0}]

path, thread_id, phase = sys.argv[1:]
checkpointer, connection = open_sqlite_checkpointer(path)
model, store = Model(revise_mode=phase == "resume_revise"), Store()
graph = build_graph(model, resume_store=store, checkpointer=checkpointer)
config = {"configurable": {"thread_id": thread_id}}
if phase == "interrupt":
    graph.invoke({"thread_id": thread_id, "user_input": JD_TEXT, "resume_version": "resume-v1"}, config=config)
    snapshot = graph.get_state(config)
    print(json.dumps({"state": snapshot.values, "interrupt": snapshot.tasks[0].interrupts[0].value, "model_calls": model.calls, "store_calls": store.calls}, default=lambda v: v.model_dump() if hasattr(v, "model_dump") else str(v)))
else:
    if phase == "resume_revise":
        command = {"action": "revise_inputs", "resume_version": "resume-v2", "feedback": "use latest resume"}
    else:
        action = "approve" if phase == "resume_approve" else "continue" if phase == "resume_continue" else "cancel"
        command = {"action": action, "feedback": "confirmed"}
    result = graph.invoke(Command(resume=command), config=config)
    snapshot = graph.get_state(config)
    interrupt = snapshot.tasks[0].interrupts[0].value if snapshot.tasks and snapshot.tasks[0].interrupts else None
    print(json.dumps({"state": result, "interrupt": interrupt, "model_calls": model.calls, "store_calls": store.calls}, default=lambda v: v.model_dump() if hasattr(v, "model_dump") else str(v)))
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


def _run_combined_child(checkpoint_path: Path, thread_id: str, phase: str) -> dict[str, object]:
    """在独立进程执行或恢复 JD+匹配组合图。"""

    completed = subprocess.run(
        [sys.executable, "-c", COMBINED_CHILD_PROGRAM, str(checkpoint_path), thread_id, phase],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout)


@pytest.mark.core_agent_tests
@pytest.mark.parametrize(
    ("resume_phase", "expected_status", "expected_node"),
    [("resume_continue", "in_review", "prepare_final_review"), ("resume_cancel", "rejected", "low_score_cancelled")],
)
def test_combined_task_low_score_checkpoint_recovers_without_replaying_workers(
    tmp_path: Path, resume_phase: str, expected_status: str, expected_node: str
) -> None:
    """验证组合队列在低分中断后跨进程恢复，不重复执行已消费任务。"""

    checkpoint_path = tmp_path / "combined-checkpoints.sqlite3"
    thread_id = f"combined-{resume_phase}"
    interrupted = _run_combined_child(checkpoint_path, thread_id, "interrupt")
    baseline = interrupted["state"]
    assert interrupted["interrupt"]["type"] == "low_match_score"
    assert baseline["task_queue"] == []
    assert interrupted["model_calls"] == 3
    assert interrupted["store_calls"] == 3

    resumed = _run_combined_child(checkpoint_path, thread_id, resume_phase)
    state = resumed["state"]
    assert state["review_status"] == expected_status
    assert state["current_node"] == expected_node
    assert resumed["model_calls"] == 0
    assert resumed["store_calls"] == 0
    history_nodes = [event["node"] for event in state["execution_history"]]
    assert history_nodes.count("jd_parser") == 1
    assert history_nodes.count("resume_matcher") == 1
    if resume_phase == "resume_continue":
        assert resumed["interrupt"]["type"] == "final_review"
        approved = _run_combined_child(checkpoint_path, thread_id, "resume_approve")
        assert approved["state"]["current_node"] == "finalize_node"
        assert approved["state"]["final_output"]["type"] == "match_result"
        assert approved["model_calls"] == 0
        assert approved["store_calls"] == 0


@pytest.mark.core_agent_tests
def test_low_score_revise_inputs_recovers_in_fresh_process_with_second_attempt(tmp_path: Path) -> None:
    """验证换简历版本的重算复用 thread_id，并留下第二次结构化评分 attempt。"""

    checkpoint_path = tmp_path / "revise-checkpoints.sqlite3"
    thread_id = "combined-revise"
    interrupted = _run_combined_child(checkpoint_path, thread_id, "interrupt")
    assert interrupted["interrupt"]["type"] == "low_match_score"

    revised = _run_combined_child(checkpoint_path, thread_id, "resume_revise")
    state = revised["state"]
    assert state["match_result"]["total_score"] == 52.0
    assert revised["interrupt"]["type"] == "low_match_score"
    assert state["match_result"]["resume_version"] == "resume-v2"
    assert revised["model_calls"] == 1
    assert revised["store_calls"] == 3
    matcher_events = [event for event in state["execution_history"] if event["node"] == "resume_matcher" and event["event"] == "success"]
    assert [event["metadata"]["business_attempt"] for event in matcher_events] == [1, 2]



@pytest.mark.core_agent_tests
@pytest.mark.parametrize(
    ("resume_phase", "expected_status", "expected_node"),
    [
        ("resume_continue", "in_review", "prepare_final_review"),
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

    resumed_payload = _run_child(checkpoint_path, thread_id, resume_phase)
    resumed = resumed_payload["state"]
    assert resumed["review_status"] == expected_status
    assert resumed["current_node"] == expected_node
    for field in ("thread_id", "jd_parsed", "match_result", "error_log", "retry_count", "conversation_summary", "summarized_message_count"):
        assert resumed[field] == baseline[field]
    assert resumed["execution_history"][: len(baseline["execution_history"])] == baseline["execution_history"]
    assert resumed.get("final_output") is None
    if resume_phase == "resume_continue":
        assert resumed_payload["interrupt"]["type"] == "final_review"

    connection = sqlite3.connect(checkpoint_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] > 0
    finally:
        connection.close()