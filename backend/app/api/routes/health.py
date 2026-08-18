from fastapi import APIRouter, Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.errors import api_error


router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request) -> dict[str, str]:
    if not hasattr(request.app.state, "runtime") or not hasattr(request.app.state, "graph_runtime"):
        raise api_error(503, "APP_NOT_READY", "应用尚未完成初始化。", retryable=True)
    try:
        with request.app.state.session_factory() as session:
            session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise api_error(503, "DATABASE_NOT_READY", "数据库暂时不可用。", retryable=True) from exc
    return {"status": "ready"}
