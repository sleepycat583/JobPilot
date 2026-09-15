"""Small, dependency-light client for QCC Streamable HTTP MCP servers."""

from __future__ import annotations

import asyncio
import inspect
import random
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol


class QccProviderError(RuntimeError):
    """Provider or client policy failure."""

    def __init__(self, message: str, *, kind: str = "provider_error", request_id: str | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.request_id = request_id


class QccTransport(Protocol):
    def __call__(
        self,
        server: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        headers: Mapping[str, str],
        timeout: float,
    ) -> Any: ...


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, QccProviderError):
        status = getattr(exc, "status_code", None)
        return status == 429 or (isinstance(status, int) and status >= 500)
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError, OSError)):
        return True
    status = getattr(exc, "status_code", getattr(exc, "status", None))
    return status == 429 or (isinstance(status, int) and status >= 500) or isinstance(exc, RuntimeError)


class QccMcpClient:
    def __init__(
        self,
        *,
        api_key: str,
        server_urls: Mapping[str, str],
        allowlist: Mapping[str, set[str] | frozenset[str]],
        transport: QccTransport | None = None,
        timeout_seconds: float = 20.0,
        max_retries: int = 2,
        max_concurrency: int = 4,
        retry_backoff_seconds: float = 0.25,
    ) -> None:
        self.api_key = api_key.strip()
        self.server_urls = dict(server_urls)
        self.allowlist = {k: set(v) for k, v in allowlist.items()}
        self.transport = transport or self._http_transport
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, min(max_retries, 2))
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))

    async def _http_transport(
        self,
        server: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        headers: Mapping[str, str],
        timeout: float,
    ) -> Any:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - dependency is present in production
            raise QccProviderError("httpx is required for QCC MCP", kind="provider_error") from exc
        url = self.server_urls[server]
        payload = {"jsonrpc": "2.0", "id": random.randint(1, 2**31 - 1), "method": "tools/call", "params": {"name": tool_name, "arguments": dict(arguments)}}
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=payload, headers=dict(headers))
            if response.status_code == 429 or response.status_code >= 500:
                error = QccProviderError(f"QCC HTTP {response.status_code}", kind="provider_error")
                setattr(error, "status_code", response.status_code)
                raise error
            response.raise_for_status()
            return response.json()

    async def call_tool(self, server: str, tool_name: str, arguments: Mapping[str, Any]) -> Any:
        if server not in self.server_urls:
            raise QccProviderError(f"unknown QCC server: {server}")
        if tool_name not in self.allowlist.get(server, set()):
            raise QccProviderError(f"tool {tool_name} is outside allowlist", kind="provider_error")
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with self._semaphore:
            for attempt in range(self.max_retries + 1):
                try:
                    result = self.transport(server, tool_name, arguments, headers, self.timeout_seconds)
                    if inspect.isawaitable(result):
                        result = await asyncio.wait_for(result, timeout=self.timeout_seconds)
                    return result
                except Exception as exc:
                    if attempt >= self.max_retries or not _is_transient(exc):
                        if isinstance(exc, QccProviderError):
                            raise
                        raise QccProviderError(str(exc), kind="provider_error") from exc
                    delay = self.retry_backoff_seconds * (2**attempt)
                    if delay:
                        await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    async def list_tools(self, server: str) -> Any:
        """Return provider tool metadata when transport supports discovery."""
        if server not in self.server_urls:
            raise QccProviderError(f"unknown QCC server: {server}")
        discover = getattr(self.transport, "list_tools", None)
        if discover is None:
            return sorted(self.allowlist.get(server, set()))
        result = discover(server, {"Authorization": f"Bearer {self.api_key}"}, self.timeout_seconds)
        if inspect.isawaitable(result):
            result = await asyncio.wait_for(result, timeout=self.timeout_seconds)
        return result


def unwrap_mcp_result(result: Any) -> Any:
    """Extract common MCP content envelopes while preserving unknown payloads."""
    if isinstance(result, Mapping):
        if "structuredContent" in result:
            return result["structuredContent"]
        if "result" in result and isinstance(result["result"], Mapping):
            inner = result["result"]
            if "structuredContent" in inner:
                return inner["structuredContent"]
            if "content" in inner:
                return inner["content"]
        if "content" in result and len(result) == 1:
            return result["content"]
    return result


def extract_request_id(result: Any) -> str | None:
    if isinstance(result, Mapping):
        for key in ("provider_request_id", "request_id", "requestId", "id"):
            value = result.get(key)
            if value is not None and isinstance(value, (str, int)):
                return str(value)
    return None
