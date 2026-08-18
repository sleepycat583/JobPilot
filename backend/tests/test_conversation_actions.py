import asyncio
from typing import Any

from fastapi.testclient import TestClient

from app.models import ThreadRecord
from app.services.store import load_thread_state, save_thread_state
from tests.test_api import create_jd, create_resume, create_thread, key, wait_for_thread


class ActionGraph:
    def __init__(self) -> None:
        self.action = "respond"
        self.worker = "chat_worker"

    def invoke(self, _input: dict[str, Any], _config: dict[str, Any]) -> dict[str, Any]:
        return {
            "worker_name": self.worker,
            "worker_result": {"kind": "worker_decision", "worker": self.worker, "action": self.action},
            "visible_output": "动作已交给 Worker。",
            "public_output": "动作已交给 Worker。",
        }


def test_chat_worker_action_runs_match_with_grounded_result(client: TestClient) -> None:
    resume_id = create_resume(client)
    jd_id = create_jd(client)
    thread_id = create_thread(client)
    graph = ActionGraph()
    graph.action = "run_match"
    graph.worker = "match_worker"
    client.app.state.graph_runtime.graph = graph

    response = client.post(
        f"/api/threads/{thread_id}/messages",
        headers=key(),
        json={"content": "请立即生成匹配报告", "resume_id": resume_id, "jd_id": jd_id},
    )
    assert response.status_code == 202
    state = wait_for_thread(client, thread_id, "completed")
    assert state["match_result"]["total_score"] == 72.5
    assert "匹配分析完成" in state["messages"][-1]["content"]


def test_chat_worker_action_creates_and_selects_jd(client: TestClient) -> None:
    thread_id = create_thread(client)
    graph = ActionGraph()
    graph.action = "create_jd"
    graph.worker = "jd_worker"
    client.app.state.graph_runtime.graph = graph

    response = client.post(
        f"/api/threads/{thread_id}/messages",
        headers=key(),
        json={"content": "招聘高级 Java 后端工程师，负责核心服务稳定性建设和性能治理。"},
    )
    assert response.status_code == 202
    state = wait_for_thread(client, thread_id, "completed")
    assert state["selected_jd_id"]
    jd = client.get(f"/api/jds/{state['selected_jd_id']}").json()
    assert jd["status"] == "completed"
    assert "JD 已解析" in state["messages"][-1]["content"]


def test_chat_worker_action_drives_interview_question_feedback_and_next_step(client: TestClient) -> None:
    resume_id = create_resume(client)
    jd_id = create_jd(client)
    thread_id = create_thread(client)
    graph = ActionGraph()
    graph.worker = "interview_worker"
    client.app.state.graph_runtime.graph = graph

    graph.action = "start_interview"
    client.post(
        f"/api/threads/{thread_id}/messages",
        headers=key(),
        json={"content": "开始模拟面试", "resume_id": resume_id, "jd_id": jd_id},
    )
    state = wait_for_thread(client, thread_id, "interrupted")
    assert state["pending_interrupt"]["type"] == "interview_answer"
    assert "第 1 题" in state["messages"][-1]["content"]

    graph.action = "submit_interview_answer"
    client.post(f"/api/threads/{thread_id}/messages", headers=key(), json={"content": "我负责服务拆分并验证了吞吐提升。"})
    state = wait_for_thread(client, thread_id, "interrupted")
    assert state["pending_interrupt"]["type"] == "interview_continue"
    assert "本题反馈" in state["messages"][-1]["content"]

    graph.action = "continue_interview"
    client.post(f"/api/threads/{thread_id}/messages", headers=key(), json={"content": "下一题"})
    state = wait_for_thread(client, thread_id, "interrupted")
    assert state["interview"]["current_index"] == 1
    assert state["pending_interrupt"]["type"] == "interview_answer"


def test_regular_reply_does_not_clear_active_interview_interrupt(client: TestClient) -> None:
    resume_id = create_resume(client)
    jd_id = create_jd(client)
    thread_id = create_thread(client)
    graph = ActionGraph()
    graph.worker = "interview_worker"
    graph.action = "start_interview"
    client.app.state.graph_runtime.graph = graph
    client.post(
        f"/api/threads/{thread_id}/messages",
        headers=key(),
        json={"content": "开始模拟面试", "resume_id": resume_id, "jd_id": jd_id},
    )
    before = wait_for_thread(client, thread_id, "interrupted")
    interrupt_id = before["pending_interrupt"]["id"]

    graph.action = "respond"
    client.post(f"/api/threads/{thread_id}/messages", headers=key(), json={"content": "请再解释一下题目"})
    after = wait_for_thread(client, thread_id, "interrupted")
    assert after["pending_interrupt"]["id"] == interrupt_id


def test_started_match_action_recovers_without_running_match_twice(client: TestClient) -> None:
    resume_id = create_resume(client)
    jd_id = create_jd(client)
    thread_id = create_thread(client)
    run_id = "recovered-match-run"
    with client.app.state.session_factory() as session:
        thread = session.get(ThreadRecord, thread_id)
        assert thread is not None
        state = load_thread_state(thread)
        state.update(
            {
                "status": "completed",
                "active_run_id": run_id,
                "selected_resume_id": resume_id,
                "selected_jd_id": jd_id,
                "conversation_action": {"run_id": run_id, "action": "run_match", "status": "started"},
                "match_result": {
                    "total_score": 80,
                    "strengths": ["Java 经验明确"],
                    "gaps": ["稳定性证据不足"],
                    "evidence_count": 1,
                },
            }
        )
        save_thread_state(session, thread, state)

    async def fail_if_called(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("recovered action must not execute matching twice")

    service = client.app.state.graph_runtime.action_service
    service.task_runtime.process_match = fail_if_called
    outcome = asyncio.run(service.execute(thread_id, run_id, "run_match", "重新匹配"))
    assert outcome is not None and outcome.status == "completed"
    assert "80/100" in outcome.message
