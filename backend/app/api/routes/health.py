from fastapi import APIRouter, Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.errors import api_error
from app.core.checkpoint import probe_checkpoint


router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request) -> dict[str, str]:
    if (
        not hasattr(request.app.state, "runtime")
        or not hasattr(request.app.state, "graph_runtime")
        or not hasattr(request.app.state, "graph_checkpointer")
    ):
        raise api_error(503, "APP_NOT_READY", "应用尚未完成初始化。", retryable=True)
    try:
        with request.app.state.session_factory() as session:
            session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise api_error(503, "DATABASE_NOT_READY", "数据库暂时不可用。", retryable=True) from exc
    try:
        probe_checkpoint(request.app.state.graph_checkpointer)
    except Exception as exc:
        raise api_error(503, "CHECKPOINT_NOT_READY", "工作流状态存储暂时不可用。", retryable=True) from exc
    try:
        request.app.state.blob_store.probe()
        vector_store = getattr(request.app.state, "vector_store", None)
        if vector_store is not None:
            vector_store.probe()
    except Exception as exc:
        raise api_error(503, "SHARED_STORAGE_NOT_READY", "文件或向量存储暂时不可用。", retryable=True) from exc
    return {"status": "ready"}
