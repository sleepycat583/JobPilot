from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import warnings

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langchain_core._api.deprecation import LangChainPendingDeprecationWarning

warnings.filterwarnings(
    "ignore",
    message="The default value of `allowed_objects` will change.*",
    category=LangChainPendingDeprecationWarning,
)

from langgraph.checkpoint.sqlite import SqliteSaver

from app.api.routes import events, health, interviews, jds, jobs, matches, resumes, threads
from app.core.config import configure_langsmith, get_settings
from app.db import Base, build_engine, build_session_factory
from app.graph import LangGraphRuntime, build_career_graph, build_model_bundle
from app.services.task_runtime import TaskRuntime
from app.services.vector_store import VectorStore


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_langsmith(settings)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    engine = build_engine(settings)
    if settings.auto_create_schema:
        Base.metadata.create_all(engine)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = build_session_factory(engine)
    settings.graph_checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_manager = SqliteSaver.from_conn_string(str(settings.graph_checkpoint_path))
    app.state.graph_checkpointer = checkpoint_manager.__enter__()
    try:
        models = build_model_bundle(settings)
        app.state.vector_store = VectorStore(settings) if settings.llm_mode == "openai" else None
        app.state.runtime = TaskRuntime(app.state.session_factory, settings, models.worker_model, app.state.vector_store)
        app.state.career_graph = build_career_graph(models, app.state.graph_checkpointer)
        app.state.graph_runtime = LangGraphRuntime(app.state.career_graph, app.state.session_factory, app.state.runtime)
        await app.state.runtime.recover_pending_jobs()
        await app.state.graph_runtime.recover_incomplete_threads()
        yield
    finally:
        if hasattr(app.state, "graph_runtime"):
            await app.state.graph_runtime.shutdown()
        if hasattr(app.state, "runtime"):
            await app.state.runtime.shutdown()
        checkpoint_manager.__exit__(None, None, None)
        engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="LangGraph Supervisor-Worker API；开发环境可使用无语义推断的 Stub 模型。",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(HTTPException)
    async def normalized_http_error(request: Request, exc: HTTPException):
        if isinstance(exc.detail, dict) and {"code", "message"}.issubset(exc.detail):
            return JSONResponse(status_code=exc.status_code, content={"error": exc.detail}, headers=exc.headers)
        return await http_exception_handler(request, exc)

    @app.exception_handler(RequestValidationError)
    async def normalized_validation_error(_request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(part) for part in first.get("loc", []) if part != "body")
        message = first.get("msg", "请求参数无效")
        if location:
            message = f"{location}: {message}"
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "VALIDATION_ERROR", "message": message, "retryable": False}},
        )

    for router in (health.router, jobs.router, resumes.router, jds.router, threads.router, events.router, matches.router, interviews.router):
        app.include_router(router, prefix=settings.api_prefix)
    return app


app = create_app()
