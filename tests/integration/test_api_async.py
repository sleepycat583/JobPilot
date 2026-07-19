"""异步任务启动与 SSE 端点集成验收。

本模块验证 ADR-001 的新异步 API 契约。SSE 响应是持续连接，测试直接消费
`StreamingResponse.body_iterator`，避免 ASGITransport 等待无限响应流结束。
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import Request
from fastapi.responses import StreamingResponse

from app.api import AppDependencies, create_app

JD_TEXT = "后端工程师岗位，要求熟悉 Java、Spring Boot，并具备三年以上接口设计经验。"


@dataclass
class BlockingGraph:
    """等待测试显式放行的 Graph，用于验证 202 不等待后台执行。"""

    release: threading.Event
    started: threading.Event

    def invoke(self, state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        del state, config
        self.started.set()
        self.release.wait(timeout=1)
        return {"final_output": {"type": "jd_parsed"}}


@dataclass
class FailingGraph:
    """立即抛出异常的 Graph，用于验证 run_failed 终态事件。"""

    def invoke(self, state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        del state, config
        raise RuntimeError("boom")


@dataclass
class CompletedGraph:
    """立即返回最终产物的 Graph，用于验证 run_completed 终态事件。"""

    def invoke(self, state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        del state, config
        return {"final_output": {"type": "jd_parsed"}}


def _sse_endpoint(app: Any) -> Any:
    """取得 session SSE 路由的端点函数，供测试直接消费持续响应。"""

    return next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/api/sessions/{session_id}/events")


def _request() -> Request:
    """创建未断开的最小 ASGI 请求，供 SSE 端点检测客户端状态。"""

    async def receive() -> dict[str, Any]:
        # SSE 生成器会轮询断开状态；测试请求始终表示连接仍存活。
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request({"type": "http", "method": "GET", "path": "/", "headers": []}, receive=receive)


async def _read_sse_event(app: Any, session_id: str) -> tuple[StreamingResponse, str]:
    """读取 SSE 端点产生的首个事件帧，不等待持续连接结束。"""

    response = await _sse_endpoint(app)(session_id, _request())
    assert isinstance(response, StreamingResponse)
    payload = await asyncio.wait_for(anext(response.body_iterator), timeout=1)
    return response, payload.decode() if isinstance(payload, bytes) else payload


@pytest.mark.asyncio
async def test_api_tasks_returns_202_without_waiting_for_graph_completion() -> None:
    release = threading.Event()
    started = threading.Event()
    app = create_app(dependencies=AppDependencies(graph=BlockingGraph(release=release, started=started)))

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            started_at = time.perf_counter()
            response = await asyncio.wait_for(client.post("/api/tasks", json={"jd_text": JD_TEXT}), timeout=0.2)
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            assert started.wait(timeout=1) is True
            release.set()

    payload = response.json()
    assert response.status_code == 202
    assert payload["status"] == "accepted"
    assert UUID(payload["session_id"]).version == 4
    assert UUID(payload["thread_id"]).version == 4
    assert elapsed_ms < 200


@pytest.mark.asyncio
async def test_api_tasks_sse_receives_run_failed_when_graph_raises() -> None:
    app = create_app(dependencies=AppDependencies(graph=FailingGraph()))

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            accepted = await client.post("/api/tasks", json={"jd_text": JD_TEXT})
        response, body = await _read_sse_event(app, accepted.json()["session_id"])
        await response.body_iterator.aclose()

    assert accepted.status_code == 202
    assert response.media_type == "text/event-stream"
    assert 'event: run_failed' in body
    assert '"event":"run_failed"' in body


@pytest.mark.asyncio
async def test_api_tasks_sse_receives_run_completed_when_final_output_exists() -> None:
    app = create_app(dependencies=AppDependencies(graph=CompletedGraph()))

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            accepted = await client.post("/api/tasks", json={"jd_text": JD_TEXT})
        response, body = await _read_sse_event(app, accepted.json()["session_id"])
        await response.body_iterator.aclose()

    assert accepted.status_code == 202
    assert response.media_type == "text/event-stream"
    assert 'event: run_completed' in body
    assert '"event":"run_completed"' in body