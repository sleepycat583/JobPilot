import asyncio
import json
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse

from app.api.errors import api_error
from app.core.event_notifier import get_event_notifier
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
        notifier = get_event_notifier()
        yield "retry: 2000\n\n"

        while not await request.is_disconnected():
            # 检查是否有新事件
            with request.app.state.session_factory() as session:
                records = events_after(
                    session,
                    stream_type="thread",
                    stream_id=thread_id,
                    after_id=cursor,
                    limit=request.app.state.settings.sse_replay_batch_size,
                )
                has_events = bool(records)
                for record in records:
                    cursor = record.id
                    yield _sse(event_id=record.id, event=record.event_type, data=loads(record.payload_json, {}))

            # 发送心跳（如果需要）
            now = time.monotonic()
            if now - heartbeat_at >= request.app.state.settings.sse_heartbeat_seconds:
                yield _sse(comment="heartbeat")
                heartbeat_at = now

            # 如果刚才没有新事件，等待通知或超时
            if not has_events:
                # 等待新事件通知，超时时间使用原有的轮询间隔
                # 这样既能实时响应，又能定期检查心跳
                await notifier.wait_for_new_event(
                    stream_type="thread",
                    stream_id=thread_id,
                    timeout=request.app.state.settings.sse_poll_interval_seconds
                )
                # 收到通知或超时后，循环会立即重新检查事件
            # 如果有事件，立即循环检查是否还有更多事件（批量处理）

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
