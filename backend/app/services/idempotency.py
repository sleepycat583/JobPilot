import hashlib
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import IdempotencyRecord
from app.services.store import dumps, loads


@dataclass(frozen=True)
class CachedResponse:
    status_code: int
    body: dict[str, Any]


class IdempotencyConflictError(ValueError):
    pass


def hash_request(*parts: bytes) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part)
    return digest.hexdigest()


def get_cached_response(
    session: Session,
    *,
    scope: str,
    key: str,
    request_hash: str,
) -> CachedResponse | None:
    record = session.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.scope == scope,
            IdempotencyRecord.key == key,
        )
    )
    if record is None:
        return None
    if record.request_hash != request_hash:
        raise IdempotencyConflictError("Idempotency-Key was already used with a different request")
    return CachedResponse(status_code=record.status_code, body=loads(record.response_json, {}))


def store_response(
    session: Session,
    *,
    scope: str,
    key: str,
    request_hash: str,
    status_code: int,
    body: dict[str, Any],
) -> None:
    session.add(
        IdempotencyRecord(
            scope=scope,
            key=key,
            request_hash=request_hash,
            status_code=status_code,
            response_json=dumps(body),
        )
    )
    session.commit()
