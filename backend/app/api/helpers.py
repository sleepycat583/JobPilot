from typing import Any

from fastapi import Header
from sqlalchemy.orm import Session

from app.api.errors import api_error
from app.services.idempotency import IdempotencyConflictError, get_cached_response


def require_idempotency_key(idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> str:
    if not idempotency_key or not idempotency_key.strip():
        raise api_error(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key 请求头不能为空。")
    if len(idempotency_key) > 200:
        raise api_error(400, "IDEMPOTENCY_KEY_INVALID", "Idempotency-Key 不能超过 200 个字符。")
    return idempotency_key.strip()


def cached_body(session: Session, *, scope: str, key: str, request_hash: str) -> tuple[int, dict[str, Any]] | None:
    try:
        cached = get_cached_response(session, scope=scope, key=key, request_hash=request_hash)
    except IdempotencyConflictError as exc:
        raise api_error(409, "IDEMPOTENCY_CONFLICT", "该 Idempotency-Key 已用于不同请求。") from exc
    if cached is None:
        return None
    return cached.status_code, cached.body
