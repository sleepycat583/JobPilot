"""FastAPI HTTP 边界。

本模块把 JD 解析与简历匹配的 LangGraph 能力暴露为同步 HTTP 接口。
生产运行时在 lifespan 中装配 Provider、Chroma 和 Graph；集成测试通过
`create_app` 注入 fake 依赖，避免真实 LLM、Embedding 或网络调用。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Literal
from uuid import UUID, uuid4

from fastapi import BackgroundTasks, FastAPI, File, Header, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from langgraph.types import Command
from sqlalchemy.orm import Session, sessionmaker
from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings, load_settings, run_startup_self_check
from app.constants import MAX_INPUT_LENGTH
from app.db import build_session_factory, create_sqlalchemy_engine, ensure_database_paths_are_isolated
from app.graph.builder import build_graph
from app.graph.checkpoint import open_sqlite_checkpointer
from app.providers.chat_model import build_chat_model
from app.providers.embedding import build_embedding_model
from app.rag.chroma_store import ChromaResumeStore
from app.repositories.resume_idempotency import ResumeIdempotencyConflictError, ResumeIdempotencyRepository
from app.repositories.resume_versions import (
    ResumeIndexStateConflictError,
    ResumeUploadIdempotencyConflictError,
    ResumeVersionRepository,
)
from app.repositories.review_audit import ReviewAuditRepository
from app.schemas.review import HITLCommand, ResumeRequest
from app.services.event_bus import SessionEventBus, SessionSubscriptionClosed
from app.services.observability import build_log_event, configure_event_publisher, configure_structured_logger, publish_run_completed_event
from app.services.resume_indexing import ResumeIndexService
from app.services.resume_storage import ResumeFileValidationError, ResumeStorage
from pydantic import TypeAdapter, ValidationError


class JobAnalysisRequest(BaseModel):
    """JD 解析及可选简历匹配请求。

    参数：
        jd_text: 待解析的 JD 原文，最小长度沿用 JD Schema 的 20 字符约束。
        resume_id: 提供时在 JD 解析成功后执行简历匹配。
    """

    model_config = ConfigDict(extra="forbid")

    jd_text: str
    resume_id: str | None = Field(default=None, min_length=1)


class ApiError(BaseModel):
    """HTTP 边界稳定错误结构。"""

    code: str
    message: str


class ResumeDto(BaseModel):
    """简历库列表与状态查询的稳定响应 DTO。"""

    resume_id: str
    display_version: int
    file_name: str
    file_size: int
    created_at: datetime
    updated_at: datetime
    index_status: Literal["pending", "indexing", "indexed", "failed"]
    error_code: str | None
    error_message: str | None


class ResumeListResponse(BaseModel):
    """简历库列表响应容器。"""

    resumes: list[ResumeDto]


@dataclass(frozen=True)
class AppDependencies:
    """FastAPI 运行所需的已装配依赖。"""

    graph: Any
    session_factory: sessionmaker[Session] | None = None
    resume_store: Any | None = None
    embedding_model: Any | None = None
    resume_storage: ResumeStorage | None = None
    close: Callable[[], None] | None = None


DependencyFactory = Callable[[Settings], AppDependencies]


def build_dependencies(settings: Settings) -> AppDependencies:
    """按冻结 Provider 边界装配生产 Graph。

    参数：
        settings: 已校验的应用配置。

    返回：
        包含已编译 LangGraph 的依赖容器。
    """

    run_startup_self_check(settings)
    ensure_database_paths_are_isolated(settings.sqlalchemy_database_url, settings.langgraph_checkpoint_path)
    chat_model = build_chat_model(settings)
    embedding_model = build_embedding_model(settings)
    resume_store = ChromaResumeStore(settings, embedding_model)
    checkpointer, connection = open_sqlite_checkpointer(settings.langgraph_checkpoint_path)
    engine = create_sqlalchemy_engine(settings.sqlalchemy_database_url)
    session_factory = build_session_factory(engine)

    def _close() -> None:
        connection.close()
        engine.dispose()

    return AppDependencies(
        graph=build_graph(chat_model, resume_store=resume_store, checkpointer=checkpointer),
        session_factory=session_factory,
        resume_store=resume_store,
        embedding_model=embedding_model,
        resume_storage=ResumeStorage(),
        close=_close,
    )


def create_app(
    *,
    dependencies: AppDependencies | None = None,
    settings: Settings | None = None,
    dependency_factory: DependencyFactory = build_dependencies,
) -> FastAPI:
    """创建最小 FastAPI 应用。

    参数：
        dependencies: 测试或嵌入式调用可直接提供的 Graph 依赖。
        settings: 生产装配使用的配置；缺省时从环境加载。
        dependency_factory: 生产依赖构造函数，便于测试替换。

    返回：
        已注册 HTTP 路由的 FastAPI 应用。
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if dependencies is not None:
            app.state.dependencies = dependencies
        else:
            app_settings = settings or load_settings()
            app.state.dependencies = dependency_factory(app_settings)
        app.state.event_bus = SessionEventBus(loop=asyncio.get_running_loop())
        background_tasks: set[asyncio.Task[Any]] = set()
        app.state.background_tasks = background_tasks
        configure_event_publisher(app.state.event_bus.publish_threadsafe)
        try:
            yield
        finally:
            configure_event_publisher(None)
            pending_tasks = list(app.state.background_tasks)
            for task in pending_tasks:
                task.cancel()
            if pending_tasks:
                await asyncio.gather(*pending_tasks, return_exceptions=True)
            if app.state.dependencies.close is not None:
                app.state.dependencies.close()

    app = FastAPI(title="Job Assistant API", version="0.1.0", lifespan=lifespan)

    @app.post("/v1/job-analysis")
    def analyze_job(request: JobAnalysisRequest, x_session_id: str | None = Header(default=None)) -> JSONResponse:
        validation_error = _validate_request(request)
        if validation_error is not None:
            return _error_response(validation_error, status_code=422)

        graph = app.state.dependencies.graph
        thread_id = str(uuid4())
        session_id = _resolve_session_id(x_session_id)
        if session_id is None:
            return _error_response(ApiError(code="SESSION_ID_INVALID", message="X-Session-ID must be a UUIDv4"), status_code=422)
        config = {"configurable": {"thread_id": thread_id, "session_id": session_id}}
        try:
            state = graph.invoke(
                {
                    "thread_id": thread_id,
                    "user_input": _build_analysis_input(request),
                    "resume_id": request.resume_id,
                },
                config=config,
            )
        except Exception:
            return _error_response(
                ApiError(code="GRAPH_EXECUTION_FAILED", message="Job analysis graph execution failed"),
                status_code=500,
            )

        return _state_response(state, thread_id=thread_id, session_id=session_id, snapshot=_safe_get_state(graph, config))

    @app.post("/api/tasks")
    async def create_task(request: JobAnalysisRequest, x_session_id: str | None = Header(default=None)) -> JSONResponse:
        validation_error = _validate_request(request)
        if validation_error is not None:
            return _error_response(validation_error, status_code=422)

        session_id = _resolve_session_id(x_session_id)
        if session_id is None:
            return _error_response(ApiError(code="SESSION_ID_INVALID", message="X-Session-ID must be a UUIDv4"), status_code=422)

        thread_id = str(uuid4())
        graph = app.state.dependencies.graph
        event_bus = app.state.event_bus
        event_bus.register_thread(session_id, thread_id)
        task = asyncio.create_task(
            _run_graph_in_background(
                graph=graph,
                event_bus=event_bus,
                thread_id=thread_id,
                session_id=session_id,
                initial_state={
                    "thread_id": thread_id,
                    "user_input": _build_analysis_input(request),
                    "resume_id": request.resume_id,
                },
                logger=configure_structured_logger(),
            )
        )
        _track_background_task(app, task)
        return JSONResponse(status_code=202, content={"session_id": session_id, "thread_id": thread_id, "status": "accepted"})

    @app.get("/v1/resumes")
    def list_resumes() -> JSONResponse:
        """读取长期简历库，按展示版本倒序返回。"""

        session_factory = app.state.dependencies.session_factory
        if session_factory is None:
            return _resume_dependencies_unavailable()
        with session_factory() as session:
            resumes = ResumeVersionRepository(session).list_versions()
            return JSONResponse(status_code=200, content={"resumes": [_resume_payload(item) for item in resumes]})

    @app.get("/v1/resumes/{resume_id}")
    def get_resume(resume_id: str) -> JSONResponse:
        """读取单个简历索引状态，供前端轮询。"""

        session_factory = app.state.dependencies.session_factory
        if session_factory is None:
            return _resume_dependencies_unavailable()
        with session_factory() as session:
            entity = ResumeVersionRepository(session).get(resume_id=resume_id)
            if entity is None:
                return _error_response(ApiError(code="RESUME_NOT_FOUND", message="Resume was not found"), status_code=404)
            return JSONResponse(status_code=200, content=_resume_payload(entity))

    @app.post("/v1/resumes")
    async def upload_resume(
        background_tasks: BackgroundTasks,
        file: UploadFile = File(...),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> JSONResponse:
        """受理 TXT 简历上传并在后台建立向量索引。"""

        dependencies = app.state.dependencies
        if (
            dependencies.session_factory is None
            or dependencies.resume_storage is None
            or dependencies.resume_store is None
            or dependencies.embedding_model is None
        ):
            return _resume_dependencies_unavailable()
        if not _is_uuid4(idempotency_key):
            return _error_response(
                ApiError(code="IDEMPOTENCY_KEY_INVALID", message="Idempotency-Key must be a UUIDv4"), status_code=422
            )
        content = await file.read()
        try:
            validated = dependencies.resume_storage.validate(file_name=file.filename or "", content=content)
        except ResumeFileValidationError as error:
            return _error_response(ApiError(code=error.code, message=str(error)), status_code=422)

        fingerprint = _resume_upload_fingerprint(validated.file_name, validated.content)
        requested_resume_id = str(uuid4())
        storage_path = dependencies.resume_storage.save(resume_id=requested_resume_id, content=validated.content)
        try:
            with dependencies.session_factory() as session:
                entity = ResumeVersionRepository(session).create_version(
                    resume_id=requested_resume_id,
                    file_name=validated.file_name,
                    file_size=validated.file_size,
                    storage_path=storage_path,
                    idempotency_key=idempotency_key,
                    request_fingerprint=fingerprint,
                )
        except ResumeUploadIdempotencyConflictError as error:
            dependencies.resume_storage.delete(storage_path)
            return _error_response(ApiError(code=str(error), message="Idempotency-Key was reused for a different upload"), status_code=409)
        except Exception:
            dependencies.resume_storage.delete(storage_path)
            return _error_response(ApiError(code="RESUME_UPLOAD_FAILED", message="Resume upload could not be accepted"), status_code=500)

        if entity.resume_id != requested_resume_id:
            dependencies.resume_storage.delete(storage_path)
            return JSONResponse(status_code=202, content=_resume_payload(entity))
        background_tasks.add_task(_run_resume_index_task, dependencies, entity.resume_id)
        return JSONResponse(status_code=202, content=_resume_payload(entity))

    @app.post("/v1/resumes/{resume_id}/retry")
    def retry_resume_index(resume_id: str, background_tasks: BackgroundTasks) -> JSONResponse:
        """重新受理失败简历的索引任务。"""

        dependencies = app.state.dependencies
        if (
            dependencies.session_factory is None
            or dependencies.resume_store is None
            or dependencies.embedding_model is None
            or dependencies.resume_storage is None
        ):
            return _resume_dependencies_unavailable()
        with dependencies.session_factory() as session:
            repository = ResumeVersionRepository(session)
            entity = repository.get(resume_id=resume_id)
            if entity is None:
                return _error_response(ApiError(code="RESUME_NOT_FOUND", message="Resume was not found"), status_code=404)
            if entity.index_status != "failed":
                return _error_response(
                    ApiError(code="RESUME_INDEX_CONFLICT", message="Only failed resumes can be retried"), status_code=409
                )
            try:
                entity = repository.mark_indexing(resume_id=resume_id)
            except ResumeIndexStateConflictError:
                return _error_response(
                    ApiError(code="RESUME_INDEX_CONFLICT", message="Resume indexing is already in progress"), status_code=409
                )
        background_tasks.add_task(_run_resume_index_task, dependencies, resume_id, False)
        return JSONResponse(status_code=202, content=_resume_payload(entity))

    @app.get("/api/sessions/{session_id}/events")
    async def stream_session_events(session_id: str, request: Request) -> StreamingResponse:
        subscription = app.state.event_bus.subscribe(session_id)

        async def event_stream():
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(subscription.next_event(), timeout=0.5)
                    except asyncio.TimeoutError:
                        continue
                    yield _format_sse_event(event)
            except SessionSubscriptionClosed:
                return
            finally:
                subscription.close("client_disconnect")

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.get("/v1/threads/{thread_id}/state")
    def get_thread_state(thread_id: str) -> JSONResponse:
        """读取线程当前状态，供刷新后的前端恢复 HITL 表单。

        返回：
            Checkpoint 存在时返回当前业务状态；仅线程不存在时返回
            `CHECKPOINT_NOT_FOUND`。完成线程以 `status=completed` 返回。
        """

        graph = app.state.dependencies.graph
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = _safe_get_state(graph, config)
        if snapshot is None or not snapshot.values:
            return _error_response(
                ApiError(code="CHECKPOINT_NOT_FOUND", message="No checkpoint exists for this thread"),
                status_code=404,
            )
        return JSONResponse(status_code=200, content=_snapshot_state_payload(snapshot, thread_id=thread_id))

    @app.post("/v1/threads/{thread_id}/resume")
    def resume_hitl(thread_id: str, payload: dict[str, Any]) -> JSONResponse:
        """恢复当前线程的低分、面试或最终核可 HITL 节点。"""

        graph = app.state.dependencies.graph
        config = {"configurable": {"thread_id": thread_id}}
        command, idempotency_key, request_error = _parse_resume_payload(payload)
        if request_error is not None:
            return _error_response(request_error, status_code=422)
        assert command is not None
        fingerprint = _command_fingerprint(command)
        record = None
        lease_reclaimed = False
        expired_audit_id: int | None = None
        if idempotency_key is not None:
            if app.state.dependencies.session_factory is None:
                return _error_response(
                    ApiError(code="IDEMPOTENCY_STORAGE_UNAVAILABLE", message="Resume idempotency storage is unavailable"),
                    status_code=500,
                )
            with app.state.dependencies.session_factory() as session:
                repository = ResumeIdempotencyRepository(session)
                record = repository.get(thread_id=thread_id, idempotency_key=idempotency_key)
                for active_record in repository.list_processing(thread_id=thread_id):
                    if record is not None and active_record.id == record.id:
                        continue
                    if not repository.reclaim_expired_lease(active_record):
                        return _error_response(
                            ApiError(code="RESUME_IN_PROGRESS", message="Another resume request is already processing for this thread"),
                            status_code=409,
                        )
                    repository.mark_failed(
                        active_record,
                        error_code="RESUME_LEASE_EXPIRED",
                        error_message="Previous resume processing lease expired before a final response was stored",
                        review_audit_id=active_record.review_audit_id,
                    )
                if record is not None:
                    if record.command_fingerprint != fingerprint:
                        return _error_response(ApiError(code="IDEMPOTENCY_KEY_REUSED", message="Idempotency key was used with a different command"), status_code=409)
                    if record.status == "succeeded" and record.response_json is not None:
                        return JSONResponse(status_code=record.http_status or 200, content=json.loads(record.response_json))
                    if record.status == "failed":
                        return _error_response(
                            ApiError(code=record.error_code or "GRAPH_EXECUTION_FAILED", message=record.error_message or "Interrupted graph resume failed"),
                            status_code=500,
                        )
                    lease_reclaimed = repository.reclaim_expired_lease(record)
                    if not lease_reclaimed:
                        return _error_response(ApiError(code="RESUME_IN_PROGRESS", message="Resume request is already processing"), status_code=409)
                    expired_audit_id = record.review_audit_id
        snapshot = _safe_get_state(graph, config)
        if snapshot is None or not snapshot.values or not snapshot.next:
            return _error_response(
                ApiError(code="CHECKPOINT_NOT_FOUND", message="No interrupted checkpoint exists for this thread"),
                status_code=404,
            )

        validated_command = _validate_hitl_command(command, snapshot)
        if isinstance(validated_command, ApiError):
            return _error_response(validated_command, status_code=422)
        session_id = _session_id_from_snapshot(snapshot)
        config["configurable"]["session_id"] = session_id
        if idempotency_key is not None and record is None:
            with app.state.dependencies.session_factory() as session:
                repository = ResumeIdempotencyRepository(session)
                try:
                    record = repository.create_processing(
                        thread_id=thread_id,
                        session_id=session_id,
                        idempotency_key=idempotency_key,
                        command_fingerprint=fingerprint,
                    )
                except ResumeIdempotencyConflictError:
                    record = repository.get(thread_id=thread_id, idempotency_key=idempotency_key)
                    if record is None:
                        return _error_response(ApiError(code="RESUME_IN_PROGRESS", message="Resume request is already processing"), status_code=409)
                    if record.command_fingerprint != fingerprint:
                        return _error_response(ApiError(code="IDEMPOTENCY_KEY_REUSED", message="Idempotency key was used with a different command"), status_code=409)
                    if record.status == "succeeded" and record.response_json is not None:
                        return JSONResponse(status_code=record.http_status or 200, content=json.loads(record.response_json))
                    return _error_response(ApiError(code="RESUME_IN_PROGRESS", message="Resume request is already processing"), status_code=409)
        audit_id: int | None = None
        if app.state.dependencies.session_factory is not None:
            interrupt_payload = _extract_interrupt(snapshot)
            with app.state.dependencies.session_factory() as session:
                repository = ReviewAuditRepository(session)
                try:
                    audit = repository.create_submitted_audit(
                        session_id=session_id,
                        thread_id=thread_id,
                        review_target=_review_target_from_interrupt(interrupt_payload),
                        action=_command_action(validated_command),
                        feedback=_command_feedback(validated_command),
                        command_json=json.dumps(validated_command.model_dump(mode="json"), ensure_ascii=False),
                        checkpoint_status_before=_checkpoint_status_before(snapshot),
                    )
                except Exception:
                    return _error_response(
                        ApiError(code="REVIEW_AUDIT_PERSISTENCE_FAILED", message="Failed to persist review audit before resume"),
                        status_code=500,
                    )
                audit_id = audit.id
            if record is not None:
                with app.state.dependencies.session_factory() as session:
                    repository = ResumeIdempotencyRepository(session)
                    attached = repository.get(thread_id=thread_id, idempotency_key=idempotency_key or "")
                    if attached is not None:
                        repository.attach_review_audit(attached, review_audit_id=audit_id)
        if lease_reclaimed and expired_audit_id is not None:
            _mark_review_audit_lease_expired(app.state.dependencies.session_factory, expired_audit_id)
        configure_structured_logger().info(
            build_log_event(
                event="run_resumed", session_id=session_id, thread_id=thread_id,
                node="api", node_kind="control", success=None,
            )
        )
        _publish_graph_run_event(
            event_bus=app.state.event_bus,
            logger=configure_structured_logger(),
            event_name="run_resumed",
            session_id=session_id,
            thread_id=thread_id,
            detail="HITL_RESUME_ACCEPTED",
            level="info",
        )
        try:
            state = graph.invoke(Command(resume=validated_command.model_dump()), config=config)
        except Exception:
            _mark_review_audit_failed(app.state.dependencies.session_factory, audit_id)
            _mark_resume_idempotency_failed(app.state.dependencies.session_factory, thread_id, idempotency_key, audit_id)
            return _error_response(
                ApiError(code="GRAPH_EXECUTION_FAILED", message="Interrupted graph resume failed"),
                status_code=500,
            )
        _mark_review_audit_succeeded(app.state.dependencies.session_factory, audit_id)
        # 发布 run_completed 事件到 SSE 总线，确保前端订阅收到终态通知
        # （后台任务在首次 interrupt 时已退出，resume handler 必须补发）
        if isinstance(state, dict) and state.get("final_output"):
            publish_run_completed_event(
                session_id=session_id,
                thread_id=thread_id,
                final_output=state["final_output"],
                logger=configure_structured_logger(),
            )
        response = _state_response(state, thread_id=thread_id, session_id=session_id, snapshot=_safe_get_state(graph, config))
        _mark_resume_idempotency_succeeded(app.state.dependencies.session_factory, thread_id, idempotency_key, response, audit_id)
        return response

    return app


def _validate_request(request: JobAnalysisRequest) -> ApiError | None:
    """校验 API 文本边界，避免无效请求进入 Graph。

    返回：
        失败时返回稳定错误码，成功时返回 `None`。
    """

    if not request.jd_text.strip():
        return ApiError(code="INPUT_EMPTY", message="jd_text must not be empty")
    if len(request.jd_text) > MAX_INPUT_LENGTH:
        return ApiError(code="INPUT_TOO_LONG", message=f"jd_text must not exceed {MAX_INPUT_LENGTH} characters")
    if len(request.jd_text) < 20:
        return ApiError(code="INPUT_TOO_SHORT", message="jd_text must contain at least 20 characters")
    return None


def _state_response(
    state: dict[str, Any],
    thread_id: str | None = None,
    session_id: str | None = None,
    snapshot: Any | None = None,
) -> JSONResponse:
    """把 LangGraph State 规范化为 API 响应。

    参数：
        state: 当前图执行的最终状态。
    返回：
        包含业务产物、审核状态和结构化错误日志的 JSON 响应。
    """

    jd_parsed = state.get("jd_parsed")
    payload = {
        "thread_id": thread_id or state.get("thread_id"),
        "session_id": session_id or "session-unavailable",
        "jd_parsed": _serialize(jd_parsed),
        "match_result": _serialize(state.get("match_result")),
        "interview_state": _serialize(state.get("interview_state")),
        "review_status": state.get("review_status"),
        "review_target": state.get("review_target"),
        "current_node": state.get("current_node"),
        "execution_history": _serialize(state.get("execution_history", [])),
        "error_log": _serialize(state.get("error_log", [])),
        "final_output": _serialize(state.get("final_output")),
    }
    interrupt_payload = _extract_interrupt(snapshot)
    if interrupt_payload is not None:
        payload["status"] = "interrupted"
        payload["interrupt"] = interrupt_payload
    elif _terminal_error_code(state) is not None:
        payload["status"] = "failed"
    else:
        payload["status"] = "completed"
    return JSONResponse(status_code=200, content=payload)


def _snapshot_state_payload(snapshot: Any, *, thread_id: str) -> dict[str, Any]:
    """将 Checkpoint snapshot 映射为刷新恢复所需的稳定线程状态 DTO。

    参数：
        snapshot: 由 `graph.get_state` 返回的 LangGraph 快照。
        thread_id: 路由参数中的线程标识。
    返回：
        可供前端判断终态或按 interrupt payload 重建审核表单的 JSON 数据。
    """

    values = getattr(snapshot, "values", {})
    if not isinstance(values, dict):
        values = {}
    interrupt_payload = _extract_interrupt(snapshot)
    status = "interrupted" if interrupt_payload is not None else "running" if getattr(snapshot, "next", ()) else "failed" if _terminal_error_code(values) is not None else "completed"
    return {
        "thread_id": thread_id,
        "session_id": _session_id_from_snapshot(snapshot),
        "status": status,
        "review_status": values.get("review_status"),
        "review_target": values.get("review_target"),
        "current_node": values.get("current_node"),
        "interrupt": _serialize(interrupt_payload) if interrupt_payload is not None else None,
        # 仅返回已写入 Checkpoint 的结构化产物，供前端在节点完成后增量呈现；
        # 不暴露原始 Prompt 或模型中间文本。
        "jd_parsed": _serialize(values.get("jd_parsed")),
        "match_result": _serialize(values.get("match_result")),
        "final_output": _serialize(values.get("final_output")),
    }


def _terminal_error_code(state: dict[str, Any]) -> str | None:
    """识别已进入 error_node 的终态失败，避免把异常结束错误标记为 completed。

    仅以 `error_node` 作为失败判据：某些可恢复业务错误会先写入 error_log，
    但后续仍可能回到正常执行路径，不能因此提前宣布整次任务失败。
    """

    if state.get("current_node") != "error_node":
        return None
    error_log = state.get("error_log")
    if not isinstance(error_log, list) or not error_log:
        return "GRAPH_EXECUTION_FAILED"
    latest = error_log[-1]
    if isinstance(latest, dict) and isinstance(latest.get("code"), str):
        return latest["code"]
    return "GRAPH_EXECUTION_FAILED"


def _resolve_session_id(candidate: str | None) -> str | None:
    """生成新 session UUID，或验证前端复用的会话关联键。"""
    if candidate is None:
        return str(uuid4())
    try:
        parsed = UUID(candidate)
    except ValueError:
        return None
    return str(parsed) if parsed.version == 4 else None


def _session_id_from_snapshot(snapshot: Any) -> str:
    """从 Checkpoint metadata 恢复 session_id，旧 Checkpoint 缺失时使用显式降级标识。"""
    metadata = getattr(snapshot, "metadata", {})
    value = metadata.get("session_id") if isinstance(metadata, dict) else None
    return value if isinstance(value, str) else "session-unavailable"


def _error_response(error: ApiError, *, status_code: int) -> JSONResponse:
    """构造不泄露内部异常的稳定 API 错误响应。"""

    return JSONResponse(status_code=status_code, content={"error": error.model_dump()})


def _resume_dependencies_unavailable() -> JSONResponse:
    """返回简历库运行依赖未完整装配时的稳定错误。"""

    return _error_response(
        ApiError(code="RESUME_SERVICE_UNAVAILABLE", message="Resume service dependencies are unavailable"),
        status_code=500,
    )


def _resume_payload(entity: Any) -> dict[str, Any]:
    """将 ORM 简历版本映射为前端可直接消费的稳定 DTO。"""

    return ResumeDto(
        resume_id=entity.resume_id,
        display_version=entity.display_version,
        file_name=entity.file_name,
        file_size=entity.file_size,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
        index_status=entity.index_status,
        error_code=entity.error_code,
        error_message=entity.error_message,
    ).model_dump(mode="json")


def _is_uuid4(value: str | None) -> bool:
    """校验上传幂等键必须是 UUIDv4，避免不同格式造成唯一键歧义。"""

    if value is None:
        return False
    try:
        return UUID(value).version == 4
    except ValueError:
        return False


def _resume_upload_fingerprint(file_name: str, content: bytes) -> str:
    """计算上传请求指纹，精确识别同一幂等键下的不同文件。

    参数：
        file_name: 已清理的 UTF-8 文件名。
        content: 原始上传字节。
    返回：
        SHA-256 十六进制摘要。
    """

    hasher = hashlib.sha256()
    hasher.update(content)
    hasher.update(b"\x00")
    hasher.update(file_name.encode("utf-8"))
    return hasher.hexdigest()


def _run_resume_index_task(dependencies: AppDependencies, resume_id: str, mark_started: bool = True) -> None:
    """在 FastAPI 后台任务中为已受理简历建立索引。

    后台任务必须创建自己的 SQLAlchemy Session，不能跨线程复用上传请求已经关闭的
    Session。索引服务内部负责把异常转换为 `failed` 状态。
    """

    if (
        dependencies.session_factory is None
        or dependencies.resume_storage is None
        or dependencies.resume_store is None
        or dependencies.embedding_model is None
    ):
        return
    with dependencies.session_factory() as session:
        ResumeIndexService(
            repository=ResumeVersionRepository(session),
            storage=dependencies.resume_storage,
            store=dependencies.resume_store,
            embedding_model=dependencies.embedding_model,
        ).index(resume_id=resume_id, mark_started=mark_started)


def _serialize(value: Any) -> Any:
    """转换 Pydantic Schema 与 State 容器为 JSON 兼容数据。"""

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    return value


def _extract_interrupt(snapshot: Any | None) -> dict[str, Any] | None:
    """从 Checkpoint 快照任务提取新版 LangGraph 的首个 interrupt payload。"""

    if snapshot is None:
        return None
    tasks = getattr(snapshot, "tasks", ())
    if not tasks or not getattr(tasks[0], "interrupts", ()):
        return None
    interrupt_event = tasks[0].interrupts[0]
    value = getattr(interrupt_event, "value", None)
    return value if isinstance(value, dict) else None


def _safe_get_state(graph: Any, config: dict[str, Any]) -> Any | None:
    """在无 Checkpointer 的注入图中保留普通 HTTP 响应兼容性。"""

    try:
        return graph.get_state(config)
    except ValueError:
        return None


def _parse_resume_payload(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None, ApiError | None]:
    """解析 resume envelope；旧格式保留给已完成态的 CHECKPOINT_NOT_FOUND 契约。"""

    if "idempotency_key" not in payload and "command" not in payload:
        return payload, None, None
    try:
        request = ResumeRequest.model_validate(payload)
    except ValidationError:
        return None, None, ApiError(code="RESUME_REQUEST_INVALID", message="Resume request requires a UUIDv4 idempotency_key and command")
    if request.idempotency_key.version != 4:
        return None, None, ApiError(code="IDEMPOTENCY_KEY_INVALID", message="idempotency_key must be a UUIDv4")
    return request.command, str(request.idempotency_key), None


def _command_fingerprint(command: dict[str, Any]) -> str:
    """计算命令稳定摘要，阻止同一幂等键被换作用途。"""

    canonical = json.dumps(command, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_hitl_command(command: dict[str, Any], snapshot: Any) -> HITLCommand | ApiError:
    """按当前 checkpoint 的 interrupt 类型校验恢复命令，拒绝跨 Gate 动作。"""

    interrupt_payload = _extract_interrupt(snapshot)
    interrupt_type = interrupt_payload.get("type") if interrupt_payload else None
    if interrupt_type not in {"low_match_score", "interview_answer", "interview_evaluation_unavailable", "final_review"}:
        return ApiError(code="INTERRUPT_PROTOCOL_INVALID", message="Interrupted checkpoint has no supported HITL type")
    candidate = {**command, "type": command.get("type", interrupt_type)}
    try:
        validated = TypeAdapter(HITLCommand).validate_python(candidate)
    except ValidationError:
        return ApiError(code="HITL_COMMAND_INVALID", message="Command does not match the interrupted HITL contract")
    if validated.type != interrupt_type:
        return ApiError(code="HITL_COMMAND_TYPE_MISMATCH", message="Command type does not match the interrupted HITL node")
    return validated


def _command_action(command: HITLCommand) -> str:
    """提取当前 HITL 命令的动作字段。"""

    return str(getattr(command, "action"))


def _command_feedback(command: HITLCommand) -> str | None:
    """提取审计需要的反馈字段。

    为什么这样做：
        只有显式反馈字段应写入 `feedback` 列；面试回答和上下文补充属于命令载荷，
        已由 `command_json` 完整保存，不应混入审核反馈语义。
    """

    feedback = getattr(command, "feedback", None)
    if isinstance(feedback, str) and feedback.strip():
        return feedback
    return None


def _review_target_from_interrupt(interrupt_payload: dict[str, Any] | None) -> str:
    """从 interrupt payload 提取审核目标。"""

    if isinstance(interrupt_payload, dict):
        target = interrupt_payload.get("target")
        if isinstance(target, str) and target:
            return target
    return "unknown"


def _checkpoint_status_before(snapshot: Any) -> str | None:
    """读取恢复前 checkpoint 中的 `review_status`。"""

    values = getattr(snapshot, "values", None)
    if isinstance(values, dict):
        status = values.get("review_status")
        return status if isinstance(status, str) and status else None
    return None


def _mark_review_audit_succeeded(session_factory: sessionmaker[Session] | None, audit_id: int | None) -> None:
    """把审计记录标记为成功完成。"""

    if session_factory is None or audit_id is None:
        return
    with session_factory() as session:
        ReviewAuditRepository(session).mark_completed(
            audit_id,
            result="succeeded",
            completed_at=datetime.now(timezone.utc),
        )


def _mark_review_audit_failed(session_factory: sessionmaker[Session] | None, audit_id: int | None) -> None:
    """把审计记录标记为恢复失败。"""

    if session_factory is None or audit_id is None:
        return
    with session_factory() as session:
        ReviewAuditRepository(session).mark_completed(
            audit_id,
            result="failed",
            result_code="GRAPH_EXECUTION_FAILED",
            error_message="Interrupted graph resume failed",
            completed_at=datetime.now(timezone.utc),
        )


def _mark_review_audit_lease_expired(session_factory: sessionmaker[Session] | None, audit_id: int) -> None:
    """审计记录显式标记租约过期，避免恢复重试无留痕。"""

    if session_factory is None:
        return
    with session_factory() as session:
        ReviewAuditRepository(session).mark_completed(
            audit_id, result="failed", result_code="RESUME_LEASE_EXPIRED",
            error_message="Resume processing lease expired before a final response was stored",
            completed_at=datetime.now(timezone.utc),
        )


def _mark_resume_idempotency_succeeded(session_factory: sessionmaker[Session] | None, thread_id: str, idempotency_key: str | None, response: JSONResponse, audit_id: int | None) -> None:
    """保存首次成功响应快照，供相同幂等键重放。"""

    if session_factory is None or idempotency_key is None:
        return
    with session_factory() as session:
        repository = ResumeIdempotencyRepository(session)
        record = repository.get(thread_id=thread_id, idempotency_key=idempotency_key)
        if record is not None:
            repository.mark_succeeded(record, http_status=response.status_code, response_json=response.body.decode("utf-8"), review_audit_id=audit_id)


def _mark_resume_idempotency_failed(session_factory: sessionmaker[Session] | None, thread_id: str, idempotency_key: str | None, audit_id: int | None) -> None:
    """持久化恢复失败，避免相同 key 再次执行未知图状态。"""

    if session_factory is None or idempotency_key is None:
        return
    with session_factory() as session:
        repository = ResumeIdempotencyRepository(session)
        record = repository.get(thread_id=thread_id, idempotency_key=idempotency_key)
        if record is not None:
            repository.mark_failed(record, error_code="GRAPH_EXECUTION_FAILED", error_message="Interrupted graph resume failed", review_audit_id=audit_id)


def _build_analysis_input(request: JobAnalysisRequest) -> str:
    """构造供 Supervisor 判断单任务或组合任务的原始用户意图。"""

    if request.resume_id is None:
        return "请解析以下岗位要求：\n" + request.jd_text
    return "请先分析以下岗位要求，再匹配指定简历：\n" + request.jd_text


def _track_background_task(app: FastAPI, task: asyncio.Task[Any]) -> None:
    """登记后台任务，并在结束后自动从应用状态移除。"""

    app.state.background_tasks.add(task)
    task.add_done_callback(app.state.background_tasks.discard)


async def _run_graph_in_background(
    *,
    graph: Any,
    event_bus: SessionEventBus,
    thread_id: str,
    session_id: str,
    initial_state: dict[str, Any],
    logger: Any,
) -> None:
    """在线程池中执行 Graph，并在调用层派生 run_failed / run_completed。

    为什么这样做：
        `run_failed` 与严格语义的 `run_completed` 属于整次 `graph.invoke()` 调用级事件，
        正确判断点在调用层而不是节点 observer。

    注意：
        此处并发安全性依赖当前 `/api/tasks` 每次都会生成新的 `thread_id`。
        因此不会触发同一 `thread_id` 被并发 invoke/resume 的逻辑竞态。
        若 Task 10 引入同一 `thread_id` 的并发 resume 场景，需要重新评估
        Checkpointer 的方法级锁是否足以覆盖整次图调用。
    """

    config = {"configurable": {"thread_id": thread_id, "session_id": session_id}}
    try:
        state = await asyncio.to_thread(graph.invoke, initial_state, config)
    except Exception:
        _publish_graph_run_event(
            event_bus=event_bus,
            logger=logger,
            event_name="run_failed",
            session_id=session_id,
            thread_id=thread_id,
            detail="GRAPH_EXECUTION_FAILED",
            level="error",
        )
        return
    terminal_error_code = _terminal_error_code(state) if isinstance(state, dict) else None
    if terminal_error_code is not None:
        _publish_graph_run_event(
            event_bus=event_bus,
            logger=logger,
            event_name="run_failed",
            session_id=session_id,
            thread_id=thread_id,
            detail=terminal_error_code,
            level="error",
        )
        return
    _maybe_publish_run_completed(
        event_bus=event_bus,
        logger=logger,
        state=state,
        session_id=session_id,
        thread_id=thread_id,
    )


def _maybe_publish_run_completed(
    *,
    event_bus: SessionEventBus,
    logger: Any,
    state: dict[str, Any],
    session_id: str,
    thread_id: str,
) -> None:
    """在 Graph 正常返回且存在最终产物时派生 `run_completed`。"""

    final_output = state.get("final_output") if isinstance(state, dict) else None
    if final_output is None:
        return
    _publish_graph_run_event(
        event_bus=event_bus,
        logger=logger,
        event_name="run_completed",
        session_id=session_id,
        thread_id=thread_id,
        detail=str(final_output.get("type", "final_output_ready")) if isinstance(final_output, dict) else "final_output_ready",
        level="info",
    )


def _publish_graph_run_event(
    *,
    event_bus: SessionEventBus,
    logger: Any,
    event_name: str,
    session_id: str,
    thread_id: str,
    detail: str,
    level: Literal["info", "error"],
) -> None:
    """构造并发布整次 Graph 调用级事件，供同步/异步调用层复用。"""

    event = build_log_event(
        event=event_name,
        session_id=session_id,
        thread_id=thread_id,
        node="api",
        node_kind="control",
        success=None,
        detail=detail,
    )
    getattr(logger, level)(event)
    event_bus.publish_threadsafe(event)


def _format_sse_event(event: dict[str, Any]) -> str:
    """把单条事件编码为标准 SSE 文本帧。"""

    event_id = str(event.get("event_id", ""))
    event_name = str(event.get("event", "message"))
    payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event_id}\nevent: {event_name}\ndata: {payload}\n\n"


app = create_app()