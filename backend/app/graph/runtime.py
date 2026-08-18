import asyncio
from collections.abc import Coroutine
from typing import Any
from uuid import uuid4

from langchain_core.messages import HumanMessage
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.graph.state import CareerGraphState
from app.models import JDRecord, ResumeRecord, ThreadRecord
from app.services.store import append_event, load_thread_state, loads, save_thread_state, utc_iso


WORKER_LABELS = {
    "resume_worker": "简历 Worker",
    "jd_worker": "JD Worker",
    "match_worker": "匹配 Worker",
    "interview_worker": "面试 Worker",
    "chat_worker": "对话 Worker",
}


class LangGraphRuntime:
    def __init__(self, graph: Any, session_factory: sessionmaker[Session]) -> None:
        self.graph = graph
        self.session_factory = session_factory
        self.tasks: set[asyncio.Task[None]] = set()

    def spawn(self, coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def shutdown(self) -> None:
        if not self.tasks:
            return
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def recover_incomplete_threads(self) -> None:
        with self.session_factory() as session:
            thread_ids = list(session.scalars(select(ThreadRecord.id).where(ThreadRecord.status == "running")))
        for thread_id in thread_ids:
            self.spawn(self._recover_thread(thread_id))

    def _config(self, thread_id: str, run_id: str) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": thread_id},
            "metadata": {"app_thread_id": thread_id, "run_id": run_id},
            "tags": ["career-workbench", "supervisor-worker"],
            "recursion_limit": 30,
        }

    def _readonly_context(self, thread_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            thread = session.get(ThreadRecord, thread_id)
            if thread is None:
                raise LookupError("Thread not found")
            state = load_thread_state(thread)
            resume = session.get(ResumeRecord, state.get("selected_resume_id")) if state.get("selected_resume_id") else None
            jd = session.get(JDRecord, state.get("selected_jd_id")) if state.get("selected_jd_id") else None
            messages = state.get("messages", [])[-10:]
            latest_user_message = next(
                (str(message.get("content", "")) for message in reversed(messages) if message.get("role") == "user"),
                "",
            )
            return {
                "latest_user_message": latest_user_message,
                "conversation_tail": [
                    {"role": str(message.get("role", "")), "content": str(message.get("content", ""))[:4_000]}
                    for message in messages
                ],
                "selected_resume": None if resume is None else {
                    "id": resume.id,
                    "display_name": resume.display_name,
                    "status": resume.status,
                    "structured": loads(resume.structured_json),
                },
                "selected_jd": None if jd is None else {
                    "id": jd.id,
                    "title": jd.title,
                    "status": jd.status,
                    "source_text": jd.source_text[:12_000],
                    "parsed": loads(jd.parsed_json),
                },
            }

    async def process_message(self, thread_id: str, run_id: str, content: str) -> None:
        try:
            with self.session_factory() as session:
                thread = session.get(ThreadRecord, thread_id)
                if thread is None:
                    return
                state = load_thread_state(thread)
                state["task"] = {
                    "status": "running",
                    "title": "Supervisor 正在路由",
                    "detail": "正在基于当前对话语义选择唯一 Worker。",
                    "steps": [
                        {"label": "读取对话上下文", "status": "done"},
                        {"label": "语义意图路由", "status": "active"},
                        {"label": "净化用户输出", "status": "pending"},
                    ],
                }
                save_thread_state(session, thread, state)
                append_event(
                    session,
                    stream_type="thread",
                    stream_id=thread_id,
                    event_type="node_started",
                    data={"run_id": run_id, "label": "Supervisor 正在理解你的需求"},
                )

            graph_input: CareerGraphState = {
                "messages": [HumanMessage(content=content)],
                "remaining_steps": 30,
                "readonly_context": self._readonly_context(thread_id),
                "run_id": run_id,
                "route_audit": None,
                "worker_name": None,
                "worker_result": None,
                "visible_output": None,
                "public_output": None,
            }
            result = await asyncio.to_thread(self.graph.invoke, graph_input, self._config(thread_id, run_id))
            await self._finalize_success(thread_id, run_id, result)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._finalize_failure(thread_id, run_id)

    async def _recover_thread(self, thread_id: str) -> None:
        with self.session_factory() as session:
            thread = session.get(ThreadRecord, thread_id)
            if thread is None:
                return
            state = load_thread_state(thread)
            run_id = str(state.get("active_run_id") or uuid4())
            latest_content = next(
                (str(item.get("content", "")) for item in reversed(state.get("messages", [])) if item.get("role") == "user"),
                "",
            )
        try:
            snapshot = await asyncio.to_thread(self.graph.get_state, self._config(thread_id, run_id))
            values = snapshot.values if snapshot else {}
            if values.get("run_id") == run_id:
                if values.get("worker_name") and values.get("public_output"):
                    await self._finalize_success(thread_id, run_id, values)
                    return
                result = await asyncio.to_thread(self.graph.invoke, None, self._config(thread_id, run_id))
                await self._finalize_success(thread_id, run_id, result)
                return
            if latest_content:
                await self.process_message(thread_id, run_id, latest_content)
            else:
                await self._finalize_failure(thread_id, run_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._finalize_failure(thread_id, run_id)

    async def _finalize_success(self, thread_id: str, run_id: str, result: dict[str, Any]) -> None:
        public_output = result.get("public_output")
        if not isinstance(public_output, str) or not public_output.strip():
            raise ValueError("Graph did not produce public_output")
        worker_name = str(result.get("worker_name") or "chat_worker")
        worker_label = WORKER_LABELS.get(worker_name, "专业 Worker")
        with self.session_factory() as session:
            thread = session.get(ThreadRecord, thread_id)
            if thread is None:
                return
            state = load_thread_state(thread)
            if state.get("active_run_id") not in (None, run_id):
                return
            message = {
                "id": str(uuid4()),
                "role": "assistant",
                "content": public_output,
                "created_at": utc_iso(),
            }
            state["messages"].append(message)
            state["status"] = "completed"
            state["task"] = {
                "status": "completed",
                "title": f"{worker_label} 已完成",
                "detail": "最终内容已通过输出净化并同步到当前会话。",
                "steps": [
                    {"label": "读取对话上下文", "status": "done"},
                    {"label": "语义意图路由", "status": "done"},
                    {"label": "净化用户输出", "status": "done"},
                ],
            }
            state.pop("active_run_id", None)
            save_thread_state(session, thread, state)
            append_event(
                session,
                stream_type="thread",
                stream_id=thread_id,
                event_type="message_completed",
                data={"run_id": run_id, "message": message},
            )
            append_event(
                session,
                stream_type="thread",
                stream_id=thread_id,
                event_type="run_completed",
                data={"run_id": run_id, "kind": "langgraph"},
            )

    async def _finalize_failure(self, thread_id: str, run_id: str) -> None:
        with self.session_factory() as session:
            thread = session.get(ThreadRecord, thread_id)
            if thread is None:
                return
            state = load_thread_state(thread)
            if state.get("active_run_id") not in (None, run_id):
                return
            state["status"] = "failed"
            state["task"] = {
                "status": "failed",
                "title": "本轮处理失败",
                "detail": "LangGraph 未能完成本轮处理，请稍后重试。",
                "steps": [],
            }
            state.pop("active_run_id", None)
            save_thread_state(session, thread, state)
            append_event(
                session,
                stream_type="thread",
                stream_id=thread_id,
                event_type="run_failed",
                data={"run_id": run_id, "message": "本轮处理失败，请稍后重试。"},
            )
