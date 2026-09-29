import asyncio
import logging
from collections.abc import Coroutine
from typing import Any
from uuid import uuid4

from langchain_core.messages import HumanMessage
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.graph.state import CareerGraphState
from app.core.observability import build_trace_config
from app.models import JDRecord, ResumeRecord, ThreadRecord
from app.services.conversation_actions import ConversationActionService, ConversationActionOutcome
from app.services.store import append_event, load_thread_state, loads, save_thread_state, utc_iso
from app.services.task_runtime import TaskRuntime
from app.services.qcc_client import QccMcpClient


WORKER_LABELS = {
    "resume_worker": "简历 Worker",
    "jd_worker": "JD Worker",
    "match_worker": "匹配 Worker",
    "interview_worker": "面试 Worker",
    "employer_worker": "雇主背调 Worker",
    "chat_worker": "对话 Worker",
}

logger = logging.getLogger(__name__)

# 结构化 Worker 只能在节点完成后得到用户可见文本，因此这里将已净化文本拆成
# 小块发送给 SSE。前端仍会按字符推进显示，避免兼容模型一次性返回时瞬间刷屏。
STREAM_DELTA_CHARS = 8
STREAM_DELTA_DELAY_SECONDS = 0.02


class LangGraphRuntime:
    def __init__(
        self,
        graph: Any,
        session_factory: sessionmaker[Session],
        task_runtime: TaskRuntime | None = None,
        qcc_client: QccMcpClient | None = None,
        report_cache_ttl_seconds: int = 86_400,
        company_tool_name: str = "search",
        risk_tool_name: str = "risk",
        company_registration_tool_name: str = "get_company_registration_info",
    ) -> None:
        self.graph = graph
        self.session_factory = session_factory
        self.action_service = (
            ConversationActionService(
                session_factory,
                task_runtime,
                qcc_client,
                report_cache_ttl_seconds,
                company_tool_name,
                risk_tool_name,
                company_registration_tool_name,
            ) if task_runtime is not None else None
        )
        self.tasks: set[asyncio.Task[None]] = set()

    def spawn(self, coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def process_employer_due_diligence(self, thread_id: str, run_id: str, content: str) -> None:
        if self.action_service is None:
            await self._finalize_failure(thread_id, run_id)
            return
        try:
            outcome = await self.action_service.execute(thread_id, run_id, "run_employer_due_diligence", content)
            if outcome is None:
                await self._finalize_failure(thread_id, run_id)
            else:
                await self._finalize_action(thread_id, run_id, outcome)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Employer due diligence run failed")
            await self._finalize_failure(thread_id, run_id)

    async def shutdown(self) -> None:
        if not self.tasks:
            return
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def recover_incomplete_threads(self) -> None:
        with self.session_factory() as session:
            thread_ids = list(session.scalars(select(ThreadRecord.id).where(ThreadRecord.status == "running")))
        for thread_id in thread_ids:
            self.spawn(self._recover_thread(thread_id))

    def _config(self, thread_id: str, run_id: str, *, operation: str = "conversation_turn") -> dict[str, Any]:
        return build_trace_config(thread_id, run_id, operation=operation)

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
                "active_interview": state.get("interview"),
                "pending_interrupt": state.get("pending_interrupt"),
                "employer_investigation": state.get("employer_investigation"),
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

            # 使用流式输出
            await self._process_message_stream(thread_id, run_id, content, graph_input)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("LangGraph conversation run failed")
            await self._finalize_failure(thread_id, run_id)

    async def _process_message_stream(
        self,
        thread_id: str,
        run_id: str,
        content: str,
        graph_input: CareerGraphState,
    ) -> None:
        """使用 LangGraph stream 模式实时推送 token,避免阻塞"""
        message_id = str(uuid4())

        # 发送消息开始事件
        with self.session_factory() as session:
            append_event(
                session,
                stream_type="thread",
                stream_id=thread_id,
                event_type="message_started",
                data={"run_id": run_id, "message_id": message_id},
            )

        config = self._config(thread_id, run_id)

        # 测试替身和旧版集成可能只提供同步 invoke；在线程中运行可保留兼容性。
        if not hasattr(self.graph, "astream"):
            result = await asyncio.to_thread(self.graph.invoke, graph_input, config)
            await self._handle_graph_result(thread_id, run_id, content, result, message_id=message_id)
            return

        # 使用 astream 异步流式处理，避免阻塞 SSE 心跳和事件发送。
        # respond Worker 会通过 custom stream 发送自然语言 chunk；结构化 action
        # 决策不会直接暴露给前端，避免半截 JSON 或工具调用污染用户消息。
        final_state = None
        streamed_content = ""

        async for chunk in self.graph.astream(graph_input, config, stream_mode=["values", "custom"]):
            if isinstance(chunk, tuple) and len(chunk) == 2:
                mode, payload = chunk
                if mode == "custom" and isinstance(payload, dict):
                    if payload.get("type") == "public_output_delta":
                        delta = payload.get("delta")
                        if isinstance(delta, str) and delta:
                            streamed_content += delta
                            with self.session_factory() as session:
                                append_event(
                                    session,
                                    stream_type="thread",
                                    stream_id=thread_id,
                                    event_type="message_delta",
                                    data={"run_id": run_id, "message_id": message_id, "delta": delta},
                                )
                elif mode == "values":
                    final_state = payload
            else:
                final_state = chunk

        # 处理最终结果
        if final_state:
            await self._handle_graph_result(
                thread_id,
                run_id,
                content,
                final_state,
                message_id=message_id,
                streamed_content=streamed_content,
            )

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
            snapshot = await asyncio.to_thread(
                self.graph.get_state,
                self._config(thread_id, run_id, operation="recovery_inspection"),
            )
            values = snapshot.values if snapshot else {}
            if values.get("run_id") == run_id:
                if values.get("worker_name") and values.get("public_output"):
                    await self._handle_graph_result(thread_id, run_id, latest_content, values)
                    return
                result = await asyncio.to_thread(
                    self.graph.invoke,
                    None,
                    self._config(thread_id, run_id, operation="recovery_resume"),
                )
                await self._handle_graph_result(thread_id, run_id, latest_content, result)
                return
            if latest_content:
                await self.process_message(thread_id, run_id, latest_content)
            else:
                await self._finalize_failure(thread_id, run_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("LangGraph thread recovery failed")
            await self._finalize_failure(thread_id, run_id)

    async def _handle_graph_result(
        self,
        thread_id: str,
        run_id: str,
        content: str,
        result: dict[str, Any],
        *,
        message_id: str | None = None,
        streamed_content: str = "",
    ) -> None:
        worker_result = result.get("worker_result") or {}
        action = str(worker_result.get("action", "respond"))
        if self.action_service is not None and action != "respond":
            outcome = await self.action_service.execute(thread_id, run_id, action, content)
            if outcome is not None:
                await self._finalize_action(thread_id, run_id, outcome, message_id=message_id)
                return
        await self._finalize_success(thread_id, run_id, result, message_id=message_id, streamed_content=streamed_content)

    async def _emit_public_output_stream(
        self,
        thread_id: str,
        run_id: str,
        message_id: str | None,
        content: str,
        *,
        already_streamed: str = "",
    ) -> None:
        """将净化后的用户可见文本拆成连续 SSE 增量事件。

        参数含义：`message_id` 用于让前端把增量归并到同一条临时消息；`content`
        必须是已经通过输出净化的文本。返回值为空，事件会直接持久化到执行事件表。
        结构化 Worker 无法安全暴露半截 JSON，因此这里是兼容不同模型供应商的
        可见文本流；后续若拆分普通文本生成和结构化决策，可替换为模型 token 流。
        """
        if not message_id or not content:
            return
        if already_streamed and content.startswith(already_streamed):
            content = content[len(already_streamed):]
        if not content:
            return
        characters = list(content)
        for start in range(0, len(characters), STREAM_DELTA_CHARS):
            delta = "".join(characters[start : start + STREAM_DELTA_CHARS])
            with self.session_factory() as session:
                append_event(
                    session,
                    stream_type="thread",
                    stream_id=thread_id,
                    event_type="message_delta",
                    data={"run_id": run_id, "message_id": message_id, "delta": delta},
                )
            if start + STREAM_DELTA_CHARS < len(characters):
                await asyncio.sleep(STREAM_DELTA_DELAY_SECONDS)

    async def _finalize_success(
        self,
        thread_id: str,
        run_id: str,
        result: dict[str, Any],
        *,
        message_id: str | None = None,
        streamed_content: str = "",
    ) -> None:
        public_output = result.get("public_output")
        if not isinstance(public_output, str) or not public_output.strip():
            raise ValueError("Graph did not produce public_output")
        worker_name = str(result.get("worker_name") or "chat_worker")
        worker_label = WORKER_LABELS.get(worker_name, "专业 Worker")
        await self._emit_public_output_stream(
            thread_id,
            run_id,
            message_id,
            public_output,
            already_streamed=streamed_content,
        )
        with self.session_factory() as session:
            thread = session.get(ThreadRecord, thread_id)
            if thread is None:
                return
            state = load_thread_state(thread)
            if state.get("status") == "cancelled" or state.get("active_run_id") not in (None, run_id):
                return
            message = {
                "id": message_id or str(uuid4()),
                "role": "assistant",
                "content": public_output,
                "created_at": utc_iso(),
            }
            state["messages"].append(message)
            if state.get("pending_interrupt"):
                state["status"] = "interrupted"
            else:
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
            state.pop("conversation_action", None)
            save_thread_state(session, thread, state)
            append_event(
                session,
                stream_type="thread",
                stream_id=thread_id,
                event_type="message_completed",
                data={"run_id": run_id, "message_id": message["id"], "message": message},
            )
            append_event(
                session,
                stream_type="thread",
                stream_id=thread_id,
                event_type="run_completed",
                data={"run_id": run_id, "kind": "langgraph"},
            )

    async def _finalize_action(
        self,
        thread_id: str,
        run_id: str,
        outcome: ConversationActionOutcome,
        *,
        message_id: str | None = None,
    ) -> None:
        from app.graph.workers import sanitize_output

        cleaned = sanitize_output({"visible_output": outcome.message}).get("public_output")
        if not isinstance(cleaned, str):
            raise ValueError("Conversation action did not produce public output")
        self._mark_action_response_streaming(thread_id, run_id)
        await self._emit_public_output_stream(thread_id, run_id, message_id, cleaned)
        with self.session_factory() as session:
            thread = session.get(ThreadRecord, thread_id)
            if thread is None:
                return
            state = load_thread_state(thread)
            if state.get("status") == "cancelled" or state.get("active_run_id") not in (None, run_id):
                return
            state["messages"].append(
                {"id": message_id or str(uuid4()), "role": "assistant", "content": cleaned, "created_at": utc_iso()}
            )
            state["status"] = outcome.status
            if not (outcome.status == "interrupted" and state.get("pending_interrupt")):
                state["task"] = {
                    "status": outcome.status,
                    "title": outcome.title,
                    "detail": outcome.detail,
                    "steps": [
                        {"label": "读取对话上下文", "status": "done"},
                        {"label": "语义意图路由", "status": "done"},
                        {"label": "执行 Worker 动作", "status": "done"},
                    ],
                }
            state.pop("active_run_id", None)
            state.pop("conversation_action", None)
            save_thread_state(session, thread, state)
            message = state["messages"][-1]
            append_event(
                session,
                stream_type="thread",
                stream_id=thread_id,
                event_type="message_completed",
                data={"run_id": run_id, "message_id": message["id"], "message": message},
            )
            if outcome.status == "completed":
                append_event(
                    session,
                    stream_type="thread",
                    stream_id=thread_id,
                    event_type="run_completed",
                    data={"run_id": run_id, "kind": "langgraph_action"},
                )
            elif outcome.status == "failed":
                append_event(
                    session,
                    stream_type="thread",
                    stream_id=thread_id,
                    event_type="run_failed",
                    data={"run_id": run_id, "message": "Worker action failed"},
                )

    def _mark_action_response_streaming(self, thread_id: str, run_id: str) -> None:
        """在动作结果开始逐步发送前暂时锁定会话，避免提前暴露终态。"""
        with self.session_factory() as session:
            thread = session.get(ThreadRecord, thread_id)
            if thread is None:
                return
            state = load_thread_state(thread)
            if state.get("status") == "cancelled" or state.get("active_run_id") not in (None, run_id):
                return
            task = dict(state.get("task") or {})
            task["status"] = "running"
            state["status"] = "running"
            state["task"] = task
            save_thread_state(session, thread, state)

    async def _finalize_failure(self, thread_id: str, run_id: str) -> None:
        with self.session_factory() as session:
            thread = session.get(ThreadRecord, thread_id)
            if thread is None:
                return
            state = load_thread_state(thread)
            if state.get("status") == "cancelled" or state.get("active_run_id") not in (None, run_id):
                return
            state["status"] = "failed"
            state["task"] = {
                "status": "failed",
                "title": "本轮处理失败",
                "detail": "LangGraph 未能完成本轮处理，请稍后重试。",
                "steps": [],
            }
            state.pop("active_run_id", None)
            state.pop("conversation_action", None)
            save_thread_state(session, thread, state)
            append_event(
                session,
                stream_type="thread",
                stream_id=thread_id,
                event_type="run_failed",
                data={"run_id": run_id, "message": "本轮处理失败，请稍后重试。"},
            )
