from __future__ import annotations

from dataclasses import dataclass
import asyncio
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy.orm import Session, sessionmaker

from app.models import JDRecord, JobRecord, ResumeRecord, ThreadRecord
from app.services.store import dumps, load_thread_state, loads, save_thread_state
from app.services.task_runtime import TaskRuntime


ActionStatus = Literal["completed", "interrupted", "failed"]


@dataclass(frozen=True)
class ConversationActionOutcome:
    status: ActionStatus
    message: str
    title: str
    detail: str


class ConversationActionService:
    """Executes actions already selected by a semantic Worker.

    This service deliberately contains no keyword or intent detection. The
    action value is produced by the Worker structured-output contract.
    """

    def __init__(self, session_factory: sessionmaker[Session], task_runtime: TaskRuntime) -> None:
        self.session_factory = session_factory
        self.task_runtime = task_runtime

    async def execute(
        self,
        thread_id: str,
        run_id: str,
        action: str,
        content: str,
    ) -> ConversationActionOutcome | None:
        if action == "respond":
            return None
        existing = self._existing_action(thread_id, run_id, action)
        if existing is not None:
            recovered = await self._recover_existing(thread_id, run_id, action, content)
            if recovered is not None:
                return recovered
        self._mark_action_started(thread_id, run_id, action)
        if action == "create_jd":
            return await self._create_jd(thread_id, run_id, content)
        if action == "run_match":
            return await self._run_match(thread_id, run_id)
        if action == "start_interview":
            return await self._start_interview(thread_id, run_id)
        if action == "submit_interview_answer":
            return await self._submit_interview_answer(thread_id, content)
        if action == "continue_interview":
            return await self._continue_interview(thread_id)
        if action == "end_interview":
            return await self._end_interview(thread_id)
        return ConversationActionOutcome(
            status="completed",
            message="我暂时无法执行这项操作，请改用对应的工作区继续。",
            title="需要补充操作",
            detail="Worker 返回了未支持的动作。",
        )

    def _existing_action(self, thread_id: str, run_id: str, action: str) -> dict[str, Any] | None:
        with self.session_factory() as session:
            thread = session.get(ThreadRecord, thread_id)
            if thread is None:
                raise LookupError("Thread not found")
            state = load_thread_state(thread)
            value = state.get("conversation_action")
            if isinstance(value, dict) and value.get("run_id") == run_id and value.get("action") == action:
                return value
        return None

    def _mark_action_started(self, thread_id: str, run_id: str, action: str) -> None:
        with self.session_factory() as session:
            thread = session.get(ThreadRecord, thread_id)
            if thread is None:
                raise LookupError("Thread not found")
            state = load_thread_state(thread)
            state["conversation_action"] = {"run_id": run_id, "action": action, "status": "started"}
            save_thread_state(session, thread, state)

    async def _recover_existing(
        self,
        thread_id: str,
        run_id: str,
        action: str,
        content: str,
    ) -> ConversationActionOutcome | None:
        with self.session_factory() as session:
            thread = session.get(ThreadRecord, thread_id)
            if thread is None:
                raise LookupError("Thread not found")
            state = load_thread_state(thread)
            if action == "run_match" and (state.get("match_result") or state.get("status") == "failed"):
                return self._match_outcome(state)
            interview = state.get("interview") or {}
            if action == "start_interview" and interview.get("run_id") == run_id:
                return self._interview_outcome(state)
            if action == "submit_interview_answer" and interview.get("phase") in {"feedback", "report"}:
                return self._interview_outcome(state)
            if action == "continue_interview" and interview.get("phase") in {"question", "report"}:
                return self._interview_outcome(state)
            if action == "end_interview" and interview.get("phase") == "report":
                return self._interview_outcome(state)
        if action == "create_jd":
            return await self._recover_created_jd(thread_id, run_id)
        return None

    def _selected_materials(self, thread_id: str) -> tuple[str | None, str | None, str | None]:
        with self.session_factory() as session:
            thread = session.get(ThreadRecord, thread_id)
            if thread is None:
                raise LookupError("Thread not found")
            state = load_thread_state(thread)
            resume_id = state.get("selected_resume_id")
            jd_id = state.get("selected_jd_id")
            resume = session.get(ResumeRecord, resume_id) if resume_id else None
            jd = session.get(JDRecord, jd_id) if jd_id else None
            if resume is None or jd is None:
                return resume_id, jd_id, "请先在当前会话关联一份简历和一份 JD。"
            if resume.status != "indexed":
                return resume_id, jd_id, "请等待当前简历完成向量索引后再执行。"
            if jd.status != "completed" or not jd.parsed_json:
                return resume_id, jd_id, "请等待当前 JD 解析完成后再执行。"
            return resume.id, jd.id, None

    async def _run_match(self, thread_id: str, run_id: str) -> ConversationActionOutcome:
        resume_id, jd_id, error = self._selected_materials(thread_id)
        if error:
            return ConversationActionOutcome("completed", error, "匹配前置条件未满足", error)
        await self.task_runtime.process_match(thread_id, run_id, False, emit_terminal_event=False)
        with self.session_factory() as session:
            thread = session.get(ThreadRecord, thread_id)
            if thread is None:
                raise LookupError("Thread not found")
            state = load_thread_state(thread)
            if state.get("status") == "failed":
                return ConversationActionOutcome(
                    "failed",
                    str(state.get("task", {}).get("detail", "匹配分析失败，请稍后重试。")),
                    "匹配分析失败",
                    "匹配 Worker 未能完成分析。",
                )
        return self._match_outcome(state, resume_id=resume_id, jd_id=jd_id)

    @staticmethod
    def _match_outcome(
        state: dict[str, Any],
        *,
        resume_id: str | None = None,
        jd_id: str | None = None,
    ) -> ConversationActionOutcome:
        result = state.get("match_result") or {}
        if state.get("status") == "failed":
            detail = str(state.get("task", {}).get("detail", "匹配分析失败，请稍后重试。"))
            return ConversationActionOutcome("failed", detail, "匹配分析失败", "匹配 Worker 未能完成分析。")
        strengths = result.get("strengths") or []
        gaps = result.get("gaps") or []
        message = (
            f"匹配分析完成，总分 {result.get('total_score', 0)}/100。\n"
            f"优势：{'、'.join(strengths[:3]) or '暂无明确优势证据'}\n"
            f"差距：{'、'.join(gaps[:3]) or '暂无明显差距'}\n"
            f"已引用 {result.get('evidence_count', 0)} 条简历证据。"
        )
        detail = "已完成简历与 JD 的证据核对。"
        if resume_id and jd_id:
            detail = f"已关联简历 {resume_id} 与 JD {jd_id}。"
        return ConversationActionOutcome("completed", message, "匹配分析完成", detail)

    async def _start_interview(self, thread_id: str, run_id: str) -> ConversationActionOutcome:
        resume_id, jd_id, error = self._selected_materials(thread_id)
        if error:
            return ConversationActionOutcome("completed", error, "面试前置条件未满足", error)
        options = {
            "resume_id": resume_id,
            "jd_id": jd_id,
            "interview_type": "综合面试",
            "question_count": 5,
            "feedback_mode": "each",
        }
        try:
            await asyncio.to_thread(self.task_runtime.start_interview, thread_id, options, run_id=run_id)
        except (LookupError, ValueError) as exc:
            return ConversationActionOutcome("failed", str(exc), "面试启动失败", "请检查当前简历和 JD 状态。")
        with self.session_factory() as session:
            thread = session.get(ThreadRecord, thread_id)
            if thread is None:
                raise LookupError("Thread not found")
            state = load_thread_state(thread)
            interview = state.get("interview") or {}
            question = str(interview.get("current_question", ""))
        return ConversationActionOutcome(
            "interrupted",
            f"模拟面试已开始。\n第 1 题：{question}",
            "面试进行中",
            "请直接在聊天中回答当前问题。",
        )

    async def _submit_interview_answer(self, thread_id: str, content: str) -> ConversationActionOutcome:
        interrupt_id, error = self._interview_interrupt(thread_id, "interview_answer")
        if error:
            return ConversationActionOutcome("completed", error, "当前没有等待中的面试问题", error)
        try:
            state = await asyncio.to_thread(
                self.task_runtime.resume_interrupt,
                thread_id,
                interrupt_id,
                "submit_answer",
                {"answer": content.strip()},
            )
        except (LookupError, ValueError) as exc:
            return ConversationActionOutcome("failed", str(exc), "回答提交失败", "当前面试状态可能已经变化。")
        return self._interview_outcome(state)

    async def _continue_interview(self, thread_id: str) -> ConversationActionOutcome:
        interrupt_id, error = self._interview_interrupt(thread_id, "interview_continue")
        if error:
            return ConversationActionOutcome("completed", error, "当前没有等待反馈", error)
        try:
            state = await asyncio.to_thread(self.task_runtime.resume_interrupt, thread_id, interrupt_id, "next", {})
        except (LookupError, ValueError) as exc:
            return ConversationActionOutcome("failed", str(exc), "面试推进失败", "当前面试状态可能已经变化。")
        return self._interview_outcome(state)

    async def _end_interview(self, thread_id: str) -> ConversationActionOutcome:
        with self.session_factory() as session:
            thread = session.get(ThreadRecord, thread_id)
            if thread is None:
                raise LookupError("Thread not found")
            state = load_thread_state(thread)
            interrupt = state.get("pending_interrupt")
            if not interrupt or "end" not in interrupt.get("accepted_actions", []):
                return ConversationActionOutcome("completed", "当前没有可以结束的面试。", "没有进行中的面试", "")
        try:
            state = await asyncio.to_thread(self.task_runtime.resume_interrupt, thread_id, interrupt["id"], "end", {})
        except (LookupError, ValueError) as exc:
            return ConversationActionOutcome("failed", str(exc), "面试结束失败", "当前面试状态可能已经变化。")
        return self._interview_outcome(state)

    def _interview_interrupt(self, thread_id: str, kind: str) -> tuple[str | None, str | None]:
        with self.session_factory() as session:
            thread = session.get(ThreadRecord, thread_id)
            if thread is None:
                raise LookupError("Thread not found")
            state = load_thread_state(thread)
            interrupt = state.get("pending_interrupt")
            if not interrupt or interrupt.get("type") != kind:
                return None, "当前面试没有处于这一步，请先查看会话中的最新状态。"
            return str(interrupt["id"]), None

    @staticmethod
    def _interview_outcome(state: dict[str, Any]) -> ConversationActionOutcome:
        interview = state.get("interview") or {}
        phase = interview.get("phase")
        if phase == "feedback":
            feedback = interview.get("feedback") or {}
            return ConversationActionOutcome(
                "interrupted",
                f"本题反馈：{feedback.get('score', 0)} 分。\n{feedback.get('title', '')}\n{feedback.get('detail', '')}",
                "本题反馈已生成",
                "可以回复“下一题”，也可以结束面试。",
            )
        if phase == "question":
            index = int(interview.get("current_index", 0)) + 1
            return ConversationActionOutcome(
                "interrupted",
                f"第 {index} 题：{interview.get('current_question', '')}",
                "面试进行中",
                "请直接在聊天中回答当前问题。",
            )
        report = interview.get("report") or {}
        return ConversationActionOutcome(
            "completed",
            f"面试复盘已生成，总分 {report.get('overall_score', 0)} 分。\n{report.get('summary', '')}",
            "面试已结束",
            "复盘报告已经生成。",
        )

    async def _create_jd(self, thread_id: str, run_id: str, content: str) -> ConversationActionOutcome:
        source_text = content.strip()
        if len(source_text) < 20:
            return ConversationActionOutcome("completed", "请把完整职位描述粘贴到当前对话中，我再为你解析。", "需要完整 JD", "职位描述至少需要包含基本职责或要求。")
        jd_id = str(uuid5(NAMESPACE_URL, f"career-agent:{thread_id}:{run_id}:jd"))
        job_id = str(uuid5(NAMESPACE_URL, f"career-agent:{thread_id}:{run_id}:jd-job"))
        with self.session_factory() as session:
            jd = session.get(JDRecord, jd_id)
            job = session.get(JobRecord, job_id)
            if jd is None:
                jd = JDRecord(id=jd_id, source_text=source_text, status="processing")
                session.add(jd)
            if job is None:
                job = JobRecord(id=job_id, kind="jd_parse", status="queued", progress=0, payload_json=dumps({"jd_id": jd_id}))
                session.add(job)
            session.commit()
        await self.task_runtime.process_jd(job_id)
        with self.session_factory() as session:
            jd = session.get(JDRecord, jd_id)
            job = session.get(JobRecord, job_id)
            if jd is None or job is None or job.status != "completed":
                return ConversationActionOutcome("failed", "JD 解析失败，请稍后重试。", "JD 解析失败", "JD Worker 未能完成结构化解析。")
            parsed = loads(jd.parsed_json, {})
            thread = session.get(ThreadRecord, thread_id)
            if thread is not None:
                state = load_thread_state(thread)
                state["selected_jd_id"] = jd_id
                save_thread_state(session, thread, state)
        return self._jd_outcome(jd, parsed)

    async def _recover_created_jd(self, thread_id: str, run_id: str) -> ConversationActionOutcome | None:
        jd_id = str(uuid5(NAMESPACE_URL, f"career-agent:{thread_id}:{run_id}:jd"))
        job_id = str(uuid5(NAMESPACE_URL, f"career-agent:{thread_id}:{run_id}:jd-job"))
        with self.session_factory() as session:
            jd = session.get(JDRecord, jd_id)
            job = session.get(JobRecord, job_id)
            if jd is None or job is None or job.status != "completed":
                return None
            parsed = loads(jd.parsed_json, {})
            thread = session.get(ThreadRecord, thread_id)
            if thread is not None:
                state = load_thread_state(thread)
                state["selected_jd_id"] = jd_id
                save_thread_state(session, thread, state)
            return self._jd_outcome(jd, parsed)

    @staticmethod
    def _jd_outcome(jd: JDRecord, parsed: dict[str, Any]) -> ConversationActionOutcome:
        skills = parsed.get("required_skills") or []
        return ConversationActionOutcome(
            "completed",
            f"JD 已解析并关联到当前会话。\n职位：{parsed.get('job_title', jd.title)}\n必备技能：{'、'.join(str(item) for item in skills[:8]) or '待确认'}",
            "JD 解析完成",
            "现在可以继续请求岗位匹配或模拟面试。",
        )
