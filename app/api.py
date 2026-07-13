"""Week1 最小 FastAPI HTTP 边界。

本模块把 JD 解析与简历匹配的 LangGraph 能力暴露为同步 HTTP 接口。
生产运行时在 lifespan 中装配 Provider、Chroma 和 Graph；集成测试通过
`create_app` 注入 fake 依赖，避免真实 LLM、Embedding 或网络调用。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Callable, Literal
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings, load_settings, run_startup_self_check
from app.constants import MAX_INPUT_LENGTH
from app.graph.builder import build_graph
from app.graph.checkpoint import open_sqlite_checkpointer
from app.providers.chat_model import build_chat_model
from app.providers.embedding import build_embedding_model
from app.rag.chroma_store import ChromaResumeStore
from app.schemas.review import LowScoreReviewCommand


class JobAnalysisRequest(BaseModel):
    """Week1 JD 解析及可选简历匹配请求。

    参数：
        jd_text: 待解析的 JD 原文，最小长度沿用 JD Schema 的 20 字符约束。
        resume_version: 提供时在 JD 解析成功后执行简历匹配。
    """

    model_config = ConfigDict(extra="forbid")

    jd_text: str
    resume_version: str | None = Field(default=None, min_length=1)


class ApiError(BaseModel):
    """HTTP 边界稳定错误结构。"""

    code: str
    message: str


@dataclass(frozen=True)
class AppDependencies:
    """FastAPI 运行所需的已装配依赖。"""

    graph: Any
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
    chat_model = build_chat_model(settings)
    embedding_model = build_embedding_model(settings)
    resume_store = ChromaResumeStore(settings, embedding_model)
    checkpointer, connection = open_sqlite_checkpointer("./data/checkpoints.sqlite3")
    return AppDependencies(
        graph=build_graph(chat_model, resume_store=resume_store, checkpointer=checkpointer),
        close=connection.close,
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
        已注册 Week1 HTTP 路由的 FastAPI 应用。
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if dependencies is not None:
            app.state.dependencies = dependencies
        else:
            app_settings = settings or load_settings()
            app.state.dependencies = dependency_factory(app_settings)
        try:
            yield
        finally:
            if app.state.dependencies.close is not None:
                app.state.dependencies.close()

    app = FastAPI(title="Job Assistant API", version="0.1.0", lifespan=lifespan)

    @app.post("/v1/job-analysis")
    def analyze_job(request: JobAnalysisRequest) -> JSONResponse:
        validation_error = _validate_request(request)
        if validation_error is not None:
            return _error_response(validation_error, status_code=422)

        graph = app.state.dependencies.graph
        thread_id = str(uuid4())
        config = {"configurable": {"thread_id": thread_id}}
        try:
            jd_state = graph.invoke(
                {
                    "thread_id": thread_id,
                    "user_input": "请解析以下岗位要求：\n" + request.jd_text,
                },
                config=config,
            )
        except Exception:
            return _error_response(
                ApiError(code="GRAPH_EXECUTION_FAILED", message="JD parsing graph execution failed"),
                status_code=500,
            )

        if request.resume_version is None or jd_state.get("jd_parsed") is None:
            return _state_response(jd_state, thread_id=thread_id, snapshot=_safe_get_state(graph, config))

        # TODO(Week2+): 当前 Graph 尚未实现 task_queue 的原生消费。
        # 临时在 API 层显式第二次调用同一个 Graph，把 JD 结果交给 matcher；完成
        # §6.3 的确定性队列调度节点后，必须删除此编排并改为单次 Graph 执行。
        try:
            match_state = graph.invoke(
                {
                    "thread_id": thread_id,
                    "user_input": "请评估这份简历与已解析岗位的匹配度。",
                    "jd_parsed": jd_state["jd_parsed"],
                    "resume_version": request.resume_version,
                },
                config=config,
            )
        except Exception:
            return _error_response(
                ApiError(code="GRAPH_EXECUTION_FAILED", message="Resume matching graph execution failed"),
                status_code=500,
            )

        return _state_response(
            match_state,
            fallback_jd_parsed=jd_state["jd_parsed"],
            thread_id=thread_id,
            snapshot=_safe_get_state(graph, config),
        )

    @app.post("/v1/threads/{thread_id}/resume")
    def resume_low_score_review(thread_id: str, command: LowScoreReviewCommand) -> JSONResponse:
        """恢复低分 Gate 的已暂停线程，仅接受 continue 或 cancel 命令。"""

        graph = app.state.dependencies.graph
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = _safe_get_state(graph, config)
        if snapshot is None or not snapshot.values or not snapshot.next:
            return _error_response(
                ApiError(code="CHECKPOINT_NOT_FOUND", message="No interrupted checkpoint exists for this thread"),
                status_code=404,
            )

        try:
            state = graph.invoke(Command(resume=command.model_dump()), config=config)
        except Exception:
            return _error_response(
                ApiError(code="GRAPH_EXECUTION_FAILED", message="Interrupted graph resume failed"),
                status_code=500,
            )
        return _state_response(state, thread_id=thread_id, snapshot=_safe_get_state(graph, config))

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
    fallback_jd_parsed: Any | None = None,
    thread_id: str | None = None,
    snapshot: Any | None = None,
) -> JSONResponse:
    """把 LangGraph State 规范化为 Week1 API 响应。

    参数：
        state: 当前图执行的最终状态。
        fallback_jd_parsed: 第二次 Graph 执行未回写 JD 时保留第一次结果。

    返回：
        包含业务产物、审核状态和结构化错误日志的 JSON 响应。
    """

    jd_parsed = state.get("jd_parsed", fallback_jd_parsed)
    payload = {
        "thread_id": thread_id or state.get("thread_id"),
        "jd_parsed": _serialize(jd_parsed),
        "match_result": _serialize(state.get("match_result")),
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
    else:
        payload["status"] = "completed"
    return JSONResponse(status_code=200, content=payload)


def _error_response(error: ApiError, *, status_code: int) -> JSONResponse:
    """构造不泄露内部异常的稳定 API 错误响应。"""

    return JSONResponse(status_code=status_code, content={"error": error.model_dump()})


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
    """在 Week1 无 Checkpointer 的注入图中保留普通 HTTP 响应兼容性。"""

    try:
        return graph.get_state(config)
    except ValueError:
        return None


app = create_app()