import asyncio
import json
from collections.abc import Coroutine
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.language_models import BaseChatModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.models import JDRecord, JobRecord, ResumeRecord, ThreadRecord
from app.services.documents import DocumentExtractionError, extract_document_text, make_text_chunks
from app.services.llm_tasks import LLMTaskService, normalize_source_text
from app.services.store import append_event, load_thread_state, loads, save_thread_state, update_job, utc_iso
from app.services.vector_store import VectorIndexNotFound, VectorStore


INTERVIEW_QUESTIONS = [
    "请介绍一个你主导或深度参与的后端项目，并说明你承担的核心职责。",
    "在这个项目中，你遇到过最复杂的性能问题是什么？你如何定位根因？",
    "如果消息重复消费，你会怎样设计幂等机制并验证它有效？",
    "请比较缓存旁路和写穿策略，并说明你在项目中的选择依据。",
    "如果让你重新设计这个系统，你最想改进哪一部分？为什么？",
]


class TaskRuntime:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        worker_model: BaseChatModel | None = None,
        vector_store: VectorStore | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.task_service = LLMTaskService(worker_model) if worker_model is not None else None
        self.vector_store = vector_store
        self.tasks: set[asyncio.Task[None]] = set()

    def spawn(self, coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def recover_pending_jobs(self) -> None:
        with self.session_factory() as session:
            jobs = list(session.scalars(select(JobRecord).where(JobRecord.status.in_(["queued", "running"]))))
        for job in jobs:
            if job.kind == "resume_parse":
                self.spawn(self.process_resume(job.id))
            elif job.kind == "jd_parse":
                self.spawn(self.process_jd(job.id))

    async def shutdown(self) -> None:
        if not self.tasks:
            return
        for task in tuple(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def _pause(self) -> None:
        await asyncio.sleep(self.settings.mock_task_delay_seconds)

    async def process_resume(self, job_id: str) -> None:
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                return
            update_job(session, job, status="running", progress=15)
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                return
            payload = loads(job.payload_json, {})
            resume = session.get(ResumeRecord, payload["resume_id"])
            if resume is None:
                update_job(session, job, status="failed", progress=100, error_code="RESUME_NOT_FOUND", error_message="Resume resource is missing")
                return
            resume_id = resume.id
            existing_structured = loads(resume.structured_json, {}) if resume.structured_json else None
            existing_status = resume.status
            file_sha256 = resume.file_sha256
            upload_path = payload.get("upload_path")

        # A completed index is idempotent. If Chroma was cleared, the persisted
        # structured result is still reusable and the index is rebuilt below.
        if existing_structured and existing_status == "indexed":
            if self.vector_store is None or await asyncio.to_thread(self.vector_store.has_resume_index, resume_id):
                with self.session_factory() as session:
                    job = session.get(JobRecord, job_id)
                    if job is not None:
                        update_job(session, job, status="completed", progress=100, result={"resume_id": resume_id})
                return

        source_text = ""
        chunks: list[str] = []
        structured = existing_structured
        if self.task_service is None:
            await self._pause()
            structured = structured or {
                "profile": "3 年后端开发经验，主要使用 Java、Spring Boot 和 MySQL。",
                "target_role": "中高级后端开发工程师",
                "years": 3,
                "education": "本科 · 软件工程",
                "locations": ["上海", "杭州"],
                "skills": ["Java", "Spring Boot", "MySQL", "Redis", "Kafka", "Docker", "LangGraph"],
                "chunk_count": 28,
                "privacy_filtered": True,
            }
        else:
            try:
                source_text = await asyncio.to_thread(extract_document_text, Path(str(upload_path)))
                chunks = await asyncio.to_thread(make_text_chunks, normalize_source_text(source_text))
                if structured is None:
                    with self.session_factory() as session:
                        job = session.get(JobRecord, job_id)
                        if job is not None:
                            update_job(session, job, status="running", progress=55)
                    structured = (
                        await asyncio.to_thread(self.task_service.parse_resume, source_text, chunk_count=len(chunks))
                    ).model_dump(mode="json")
            except DocumentExtractionError as exc:
                with self.session_factory() as session:
                    job = session.get(JobRecord, job_id)
                    if job is not None:
                        update_job(session, job, status="failed", progress=100, error_code="RESUME_EXTRACTION_FAILED", error_message=str(exc))
                return
            except Exception:
                with self.session_factory() as session:
                    job = session.get(JobRecord, job_id)
                    if job is not None:
                        update_job(session, job, status="failed", progress=100, error_code="RESUME_MODEL_FAILED", error_message="简历结构化分析失败，请稍后重试。")
                return

        if structured is None:
            return
        with self.session_factory() as session:
            resume = session.get(ResumeRecord, resume_id)
            job = session.get(JobRecord, job_id)
            if resume is None or job is None:
                return
            resume.structured_json = json.dumps(structured, ensure_ascii=False)
            resume.status = "parsed" if self.task_service is not None else "indexed"
            session.add(resume)
            session.commit()

        if self.task_service is None or self.vector_store is None:
            with self.session_factory() as session:
                job = session.get(JobRecord, job_id)
                if job is not None:
                    update_job(session, job, status="completed", progress=100, result={"resume_id": resume_id})
            return

        try:
            indexed_count = await asyncio.to_thread(self.vector_store.index_resume, resume_id, file_sha256, chunks)
        except Exception:
            with self.session_factory() as session:
                resume = session.get(ResumeRecord, resume_id)
                job = session.get(JobRecord, job_id)
                if resume is not None:
                    resume.status = "parsed"
                    session.add(resume)
                if job is not None:
                    update_job(session, job, status="failed", progress=100, error_code="RESUME_INDEX_FAILED", error_message="简历向量索引失败，请稍后重试。")
            return
        with self.session_factory() as session:
            resume = session.get(ResumeRecord, resume_id)
            job = session.get(JobRecord, job_id)
            if resume is None or job is None:
                return
            resume.status = "indexed"
            session.add(resume)
            update_job(session, job, status="completed", progress=100, result={"resume_id": resume_id, "indexed_chunks": indexed_count})

    async def process_jd(self, job_id: str) -> None:
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                return
            update_job(session, job, status="running", progress=25)
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                return
            payload = loads(job.payload_json, {})
            jd = session.get(JDRecord, payload["jd_id"])
            if jd is None:
                update_job(session, job, status="failed", progress=100, error_code="JD_NOT_FOUND", error_message="JD resource is missing")
                return
            if jd.parsed_json and jd.status == "completed":
                update_job(session, job, status="completed", progress=100, result={"jd_id": jd.id})
                return
            if self.task_service is None:
                await self._pause()
                parsed = {
                    "job_title": "高级 Java 后端工程师",
                    "seniority": "mid_senior",
                    "responsibilities": [
                        "负责交易平台核心模块的设计、开发与稳定性建设。",
                        "推进服务性能治理，定位高并发场景下的系统瓶颈。",
                        "参与技术方案评审，并持续改善工程质量。",
                    ],
                    "required_skills": ["Java", "Spring Boot", "MySQL", "Redis", "消息队列"],
                    "preferred_skills": ["高并发调优", "交易系统经验"],
                    "inferred_skills": ["故障排查能力"],
                    "interview_focus": ["Spring Boot", "MySQL", "Redis", "消息可靠性"],
                    "evidence_preserved": True,
                }
            else:
                try:
                    update_job(session, job, status="running", progress=55)
                    parsed = (await asyncio.to_thread(self.task_service.parse_jd, jd.source_text)).model_dump(mode="json")
                except Exception:
                    update_job(session, job, status="failed", progress=100, error_code="JD_MODEL_FAILED", error_message="JD 结构化分析失败，请稍后重试。")
                    return
            jd.title = parsed["job_title"]
            jd.parsed_json = json.dumps(parsed, ensure_ascii=False)
            jd.status = "completed"
            session.add(jd)
            update_job(session, job, status="completed", progress=100, result={"jd_id": jd.id})

    async def process_match(self, thread_id: str, run_id: str, strict: bool, *, emit_terminal_event: bool = True) -> None:
        if self.task_service is not None:
            await self._process_real_match(thread_id, run_id, strict, emit_terminal_event=emit_terminal_event)
            return
        with self.session_factory() as session:
            thread = session.get(ThreadRecord, thread_id)
            if thread is None:
                return
            append_event(session, stream_type="thread", stream_id=thread_id, event_type="node_started", data={"run_id": run_id, "label": "正在检索简历证据"})
        await self._pause()

        with self.session_factory() as session:
            thread = session.get(ThreadRecord, thread_id)
            if thread is None:
                return
            state = load_thread_state(thread)
            score = 54.0 if strict else 72.5
            result = {
                "total_score": score,
                "dimension_scores": {
                    "必备技能": 25 if strict else 32,
                    "核心职责": 14 if strict else 20,
                    "加分技能": 5 if strict else 6,
                    "硬性条件": 7 if strict else 8,
                    "证据质量": 3 if strict else 6.5,
                },
                "strengths": ["Java 与 Spring Boot 经验明确", "具备消息队列工程经验", "有可量化的性能结果"],
                "gaps": ["交易系统经验表达不完整", "Redis 实战证据偏弱", "技术决策过程不足"],
                "evidence_count": 12,
                "low_score_review_required": strict,
            }
            state["match_result"] = result
            if strict:
                interrupt_id = str(uuid4())
                state["status"] = "interrupted"
                state["pending_interrupt"] = {
                    "id": interrupt_id,
                    "type": "low_match_score",
                    "title": "匹配分数较低",
                    "detail": "匹配分数低于 60，任务已暂停。",
                    "accepted_actions": ["continue", "change_materials", "cancel"],
                    "data": {"score": score, "threshold": 60},
                }
                state["task"] = {
                    "status": "interrupted",
                    "title": "需要你的确认",
                    "detail": "匹配分数低于 60，任务已暂停。",
                    "steps": [
                        {"label": "校验资料版本", "status": "done"},
                        {"label": "检索简历证据", "status": "done"},
                        {"label": "等待用户确认", "status": "active"},
                    ],
                }
                save_thread_state(session, thread, state)
                if emit_terminal_event:
                    append_event(session, stream_type="thread", stream_id=thread_id, event_type="interrupt_required", data={"run_id": run_id, "interrupt": state["pending_interrupt"]})
            else:
                state["status"] = "completed"
                state["task"] = {
                    "status": "completed",
                    "title": "匹配分析完成",
                    "detail": "已生成 12 条可追溯证据。",
                    "steps": [
                        {"label": "校验资料版本", "status": "done"},
                        {"label": "检索简历证据", "status": "done"},
                        {"label": "计算维度得分", "status": "done"},
                    ],
                }
                save_thread_state(session, thread, state)
                if emit_terminal_event:
                    append_event(session, stream_type="thread", stream_id=thread_id, event_type="run_completed", data={"run_id": run_id, "kind": "match"})

    async def _process_real_match(
        self,
        thread_id: str,
        run_id: str,
        strict: bool,
        *,
        emit_terminal_event: bool = True,
    ) -> None:
        with self.session_factory() as session:
            thread = session.get(ThreadRecord, thread_id)
            if thread is None:
                return
            state = load_thread_state(thread)
            resume = session.get(ResumeRecord, state.get("selected_resume_id")) if state.get("selected_resume_id") else None
            jd = session.get(JDRecord, state.get("selected_jd_id")) if state.get("selected_jd_id") else None
            if resume is None or jd is None:
                state["status"] = "failed"
                state["task"] = {"status": "failed", "title": "匹配分析失败", "detail": "简历或 JD 缺少结构化结果。", "steps": []}
                save_thread_state(session, thread, state)
                if emit_terminal_event:
                    append_event(session, stream_type="thread", stream_id=thread_id, event_type="run_failed", data={"run_id": run_id, "message": "匹配资料不完整"})
                return
            resume_data = loads(resume.structured_json, {})
            jd_data = loads(jd.parsed_json, {})
            jd_source_text = jd.source_text
            if not resume_data or not jd_data:
                state["status"] = "failed"
                state["task"] = {"status": "failed", "title": "匹配分析失败", "detail": "请等待简历和 JD 解析完成后重试。", "steps": []}
                save_thread_state(session, thread, state)
                if emit_terminal_event:
                    append_event(session, stream_type="thread", stream_id=thread_id, event_type="run_failed", data={"run_id": run_id, "message": "结构化资料尚未完成"})
                return
            append_event(session, stream_type="thread", stream_id=thread_id, event_type="node_started", data={"run_id": run_id, "label": "正在检索简历证据"})
        evidence_text = ""
        try:
            if self.vector_store is not None:
                query_text = "\n".join(
                    [
                        *[str(item) for item in jd_data.get("required_skills", [])],
                        *[str(item) for item in jd_data.get("responsibilities", [])],
                        *[str(item) for item in jd_data.get("preferred_skills", [])],
                        jd_source_text[:8_000],
                    ]
                )
                retrieved = await asyncio.to_thread(self.vector_store.query_resume, query_text, resume.id, 12)
                evidence_text = "\n".join(f"[{item.metadata.get('chunk_index', '?')}] {item.text}" for item in retrieved)
                if not evidence_text:
                    raise VectorIndexNotFound(resume.id)
                result = await asyncio.to_thread(self.task_service.match, resume_data, jd_data, evidence_text)
            else:
                # Keeps the service contract compatible with offline test doubles.
                result = await asyncio.to_thread(self.task_service.match, resume_data, jd_data)
        except VectorIndexNotFound:
            with self.session_factory() as session:
                thread = session.get(ThreadRecord, thread_id)
                if thread is None:
                    return
                state = load_thread_state(thread)
                state["status"] = "failed"
                state["task"] = {"status": "failed", "title": "匹配分析失败", "detail": "简历尚未完成向量索引，请等待索引任务完成后重试。", "steps": []}
                save_thread_state(session, thread, state)
                if emit_terminal_event:
                    append_event(session, stream_type="thread", stream_id=thread_id, event_type="run_failed", data={"run_id": run_id, "message": "Resume vector index is missing"})
            return
        except Exception:
            with self.session_factory() as session:
                thread = session.get(ThreadRecord, thread_id)
                if thread is None:
                    return
                state = load_thread_state(thread)
                state["status"] = "failed"
                state["task"] = {"status": "failed", "title": "匹配分析失败", "detail": "模型未能生成可靠的匹配结果，请稍后重试。", "steps": []}
                save_thread_state(session, thread, state)
                if emit_terminal_event:
                    append_event(session, stream_type="thread", stream_id=thread_id, event_type="run_failed", data={"run_id": run_id, "message": "匹配模型调用失败"})
            return

        result_data = result.model_dump(mode="json")
        score = float(result_data.get("total_score", 0))
        should_interrupt = strict and score < 60
        with self.session_factory() as session:
            thread = session.get(ThreadRecord, thread_id)
            if thread is None:
                return
            state = load_thread_state(thread)
            state["match_result"] = result_data
            if should_interrupt:
                interrupt_id = str(uuid4())
                state["status"] = "interrupted"
                state["pending_interrupt"] = {
                    "id": interrupt_id,
                    "type": "low_match_score",
                    "title": "匹配分数较低",
                    "detail": "匹配分数低于 60，任务已暂停。",
                    "accepted_actions": ["continue", "change_materials", "cancel"],
                    "data": {"score": score, "threshold": 60},
                }
                state["task"] = {
                    "status": "interrupted",
                    "title": "需要你的确认",
                    "detail": "匹配分数低于 60，任务已暂停。",
                    "steps": [
                        {"label": "校验资料版本", "status": "done"},
                        {"label": "生成匹配证据", "status": "done"},
                        {"label": "等待用户确认", "status": "active"},
                    ],
                }
                save_thread_state(session, thread, state)
                if emit_terminal_event:
                    append_event(session, stream_type="thread", stream_id=thread_id, event_type="interrupt_required", data={"run_id": run_id, "interrupt": state["pending_interrupt"]})
            else:
                state["status"] = "completed"
                state["pending_interrupt"] = None
                state["task"] = {
                    "status": "completed",
                    "title": "匹配分析完成",
                    "detail": f"已生成 {int(result_data.get('evidence_count', 0))} 条可追溯证据。",
                    "steps": [
                        {"label": "校验资料版本", "status": "done"},
                        {"label": "生成匹配证据", "status": "done"},
                        {"label": "计算维度得分", "status": "done"},
                    ],
                }
                save_thread_state(session, thread, state)
                if emit_terminal_event:
                    append_event(session, stream_type="thread", stream_id=thread_id, event_type="run_completed", data={"run_id": run_id, "kind": "match"})

    def start_interview(self, thread_id: str, options: dict[str, Any], *, run_id: str | None = None) -> str:
        run_id = run_id or str(uuid4())
        with self.session_factory() as session:
            thread = session.get(ThreadRecord, thread_id)
            if thread is None:
                raise LookupError("Thread not found")
            state = load_thread_state(thread)
            count = options["question_count"]
            interrupt_id = str(uuid4())
            if self.task_service is None:
                first_question = INTERVIEW_QUESTIONS[0]
            else:
                resume_data, jd_data = self._interview_materials(session, state, options)
                try:
                    first_question = self.task_service.generate_question(
                        options["interview_type"], resume_data, jd_data, []
                    ).question
                except Exception as exc:
                    raise ValueError("模型未能生成面试问题，请稍后重试。") from exc
            state["status"] = "interrupted"
            state["selected_resume_id"] = options["resume_id"]
            state["selected_jd_id"] = options["jd_id"]
            state["interview"] = {
                "run_id": run_id,
                "interview_type": options["interview_type"],
                "question_count": count,
                "feedback_mode": options["feedback_mode"],
                "current_index": 0,
                "phase": "question",
                "current_question": first_question,
                "records": [],
                "feedback": None,
                "report": None,
            }
            state["pending_interrupt"] = self._question_interrupt(interrupt_id, state["interview"])
            state["task"] = {
                "status": "interrupted",
                "title": "面试进行中",
                "detail": "等待你回答第 1 题。",
                "steps": [
                    {"label": "生成面试计划", "status": "done"},
                    {"label": "第 1 题", "status": "active"},
                    {"label": "最终复盘", "status": "pending"},
                ],
            }
            save_thread_state(session, thread, state)
            append_event(session, stream_type="thread", stream_id=thread_id, event_type="interrupt_required", data={"run_id": run_id, "interrupt": state["pending_interrupt"]})
        return run_id

    def resume_interrupt(self, thread_id: str, interrupt_id: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self.session_factory() as session:
            thread = session.get(ThreadRecord, thread_id)
            if thread is None:
                raise LookupError("Thread not found")
            state = load_thread_state(thread)
            interrupt = state.get("pending_interrupt")
            if not interrupt or interrupt["id"] != interrupt_id:
                raise ValueError("Interrupt is no longer active")
            if action not in interrupt["accepted_actions"]:
                raise ValueError("Action is not accepted by this interrupt")

            if interrupt["type"] == "low_match_score":
                state["pending_interrupt"] = None
                state["status"] = "completed"
                state["task"] = {
                    "status": "completed",
                    "title": "已处理你的选择",
                    "detail": "继续准备" if action == "continue" else "本次匹配已结束",
                    "steps": [],
                }
                save_thread_state(session, thread, state)
                append_event(session, stream_type="thread", stream_id=thread_id, event_type="run_resumed", data={"action": action})
                return state

            interview = state.get("interview")
            if not interview:
                raise ValueError("Interview state is missing")
            if action == "end":
                return self._finish_interview(session, thread, state)
            if action == "skip":
                return self._advance_interview(session, thread, state)
            if action == "submit_answer":
                answer = str(payload.get("answer", "")).strip()
                if not answer:
                    raise ValueError("Answer is required")
                if self.task_service is None:
                    feedback = {
                        "score": 78,
                        "title": "回答结构清楚，可以补充更多决策证据",
                        "detail": "你说明了背景、行动和结果，但还缺少替代方案比较。",
                        "tags": ["结构清晰", "结果可量化", "决策依据不足"],
                    }
                else:
                    resume_data, jd_data = self._interview_materials(session, state)
                    try:
                        feedback = self.task_service.evaluate_answer(
                            interview["interview_type"],
                            interview["current_question"],
                            answer,
                            resume_data,
                            jd_data,
                        ).model_dump(mode="json")
                    except Exception as exc:
                        raise ValueError("模型未能评估本题回答，请稍后重试。") from exc
                interview["records"].append({
                    "question": interview["current_question"],
                    "answer": answer,
                    "score": round(float(feedback["score"]), 1),
                })
                if interview["feedback_mode"] == "each":
                    interview["phase"] = "feedback"
                    interview["feedback"] = feedback
                    next_interrupt_id = str(uuid4())
                    state["pending_interrupt"] = {
                        "id": next_interrupt_id,
                        "type": "interview_continue",
                        "title": "本题反馈",
                        "detail": "查看反馈后进入下一题。",
                        "accepted_actions": ["next", "end"],
                        "data": interview["feedback"],
                    }
                    state["task"] = {
                        "status": "interrupted",
                        "title": "本题反馈已生成",
                        "detail": "查看反馈后，可以进入下一题或结束面试。",
                        "steps": [
                            {"label": "生成面试计划", "status": "done"},
                            {"label": f"第 {interview['current_index'] + 1} 题反馈", "status": "active"},
                            {"label": "最终复盘", "status": "pending"},
                        ],
                    }
                    save_thread_state(session, thread, state)
                    append_event(session, stream_type="thread", stream_id=thread_id, event_type="interrupt_required", data={"interrupt": state["pending_interrupt"]})
                    return state
                return self._advance_interview(session, thread, state)
            if action == "next":
                return self._advance_interview(session, thread, state)
            raise ValueError("Unsupported interview action")

    def _advance_interview(self, session: Session, thread: ThreadRecord, state: dict[str, Any]) -> dict[str, Any]:
        interview = state["interview"]
        next_index = interview["current_index"] + 1
        if next_index >= interview["question_count"]:
            return self._finish_interview(session, thread, state)
        interview["current_index"] = next_index
        interview["phase"] = "question"
        interview["feedback"] = None
        if self.task_service is None:
            interview["current_question"] = INTERVIEW_QUESTIONS[next_index]
        else:
            resume_data, jd_data = self._interview_materials(session, state)
            try:
                interview["current_question"] = self.task_service.generate_question(
                    interview["interview_type"], resume_data, jd_data, interview["records"]
                ).question
            except Exception as exc:
                raise ValueError("模型未能生成下一题，请稍后重试。") from exc
        state["pending_interrupt"] = self._question_interrupt(str(uuid4()), interview)
        state["task"] = {
            "status": "interrupted",
            "title": "面试进行中",
            "detail": f"等待你回答第 {next_index + 1} 题。",
            "steps": [
                {"label": "生成面试计划", "status": "done"},
                {"label": f"第 {next_index + 1} 题", "status": "active"},
                {"label": "最终复盘", "status": "pending"},
            ],
        }
        save_thread_state(session, thread, state)
        append_event(session, stream_type="thread", stream_id=thread.id, event_type="interrupt_required", data={"interrupt": state["pending_interrupt"]})
        return state

    def _finish_interview(self, session: Session, thread: ThreadRecord, state: dict[str, Any]) -> dict[str, Any]:
        interview = state["interview"]
        interview["phase"] = "report"
        if self.task_service is None:
            interview["report"] = {
                "overall_score": 76,
                "summary": "整体表现稳定，项目证据仍需加强",
                "dimension_scores": {"技术准确性": 82, "表达结构": 78, "岗位针对性": 74, "证据充分度": 68},
                "actions": ["补充技术决策证据", "加强 Redis 场景准备"],
            }
        else:
            resume_data, jd_data = self._interview_materials(session, state)
            try:
                interview["report"] = self.task_service.report(
                    interview["interview_type"], interview["records"], resume_data, jd_data
                ).model_dump(mode="json")
            except Exception as exc:
                raise ValueError("模型未能生成面试复盘，请稍后重试。") from exc
        state["pending_interrupt"] = None
        state["status"] = "completed"
        state["task"] = {
            "status": "completed",
            "title": "面试已结束",
            "detail": "复盘报告已经生成。",
            "steps": [
                {"label": "生成面试计划", "status": "done"},
                {"label": f"完成 {len(interview['records'])} 题", "status": "done"},
                {"label": "最终复盘", "status": "done"},
            ],
        }
        save_thread_state(session, thread, state)
        append_event(session, stream_type="thread", stream_id=thread.id, event_type="run_completed", data={"kind": "interview"})
        return state

    @staticmethod
    def _interview_materials(
        session: Session,
        state: dict[str, Any],
        options: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        resume_id = (options or {}).get("resume_id") or state.get("selected_resume_id")
        jd_id = (options or {}).get("jd_id") or state.get("selected_jd_id")
        resume = session.get(ResumeRecord, resume_id) if resume_id else None
        jd = session.get(JDRecord, jd_id) if jd_id else None
        resume_data = loads(resume.structured_json, {}) if resume is not None else {}
        jd_data = loads(jd.parsed_json, {}) if jd is not None else {}
        if not resume_data or not jd_data:
            raise ValueError("请等待简历和 JD 解析完成后再开始面试。")
        return resume_data, jd_data

    @staticmethod
    def _question_interrupt(interrupt_id: str, interview: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": interrupt_id,
            "type": "interview_answer",
            "title": f"第 {interview['current_index'] + 1} 题",
            "detail": interview["current_question"],
            "accepted_actions": ["submit_answer", "skip", "end"],
            "data": {
                "question": interview["current_question"],
                "index": interview["current_index"],
                "count": interview["question_count"],
            },
        }
