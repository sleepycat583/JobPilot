from fastapi import APIRouter, Request
from sqlalchemy import text


router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request) -> dict[str, str]:
    with request.app.state.session_factory() as session:
        session.execute(text("SELECT 1"))
    return {"status": "ready"}
