import asyncio
import json
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse

from app.api.errors import api_error
from app.models import ThreadRecord
from app.services.store import events_after, loads


router = APIRouter(prefix="/threads", tags=["events"])


def _sse(*, event_id: int | None = None, event: str | None = None, data: dict | None = None, comment: str | None = None) -> str:
    if comment is not None:
        return f": {comment}\n\n"
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    if event:
        lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data or {}, ensure_ascii=False, separators=(',', ':'))}")
    return "\n".join(lines) + "\n\n"


@router.get("/{thread_id}/events")
async def thread_events(
    thread_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    with request.app.state.session_factory() as session:
        if session.get(ThreadRecord, thread_id) is None:
            raise api_error(404, "THREAD_NOT_FOUND", "会话不存在。")
    try:
        cursor = max(0, int(last_event_id or "0"))
    except ValueError as exc:
        raise api_error(400, "LAST_EVENT_ID_INVALID", "Last-Event-ID 必须是非负整数。") from exc

    async def stream() -> AsyncIterator[str]:
        nonlocal cursor
        heartbeat_at = time.monotonic()
        yield "retry: 2000\n\n"
        while not await request.is_disconnected():
            with request.app.state.session_factory() as session:
                records = events_after(
                    session,
                    stream_type="thread",
                    stream_id=thread_id,
                    after_id=cursor,
                    limit=request.app.state.settings.sse_replay_batch_size,
                )
                for record in records:
                    cursor = record.id
                    yield _sse(event_id=record.id, event=record.event_type, data=loads(record.payload_json, {}))
            now = time.monotonic()
            if now - heartbeat_at >= request.app.state.settings.sse_heartbeat_seconds:
                yield _sse(comment="heartbeat")
                heartbeat_at = now
            await asyncio.sleep(request.app.state.settings.sse_poll_interval_seconds)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
