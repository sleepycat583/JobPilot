"""SSE 事件总线基础设施。

本模块实现 ADR-001 规定的进程内事件总线：
- 按 `thread_id` 保存短期事件缓冲；
- 维护 `session_id <-> thread_id` 双向索引；
- 以 `session_id` 为粒度广播实时事件；
- 提供供工作线程调用的线程安全发布入口。

本模块不依赖 FastAPI、Graph 拓扑或具体 Agent 业务逻辑，仅提供可单元测试的纯内存服务。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Callable

DEFAULT_BUFFER_WINDOW_SECONDS = 60.0
DEFAULT_BUFFER_EVENT_LIMIT = 50
DEFAULT_SUBSCRIBER_QUEUE_LIMIT = 100
_TERMINAL_EVENTS = {"run_completed", "run_failed"}


class EventBusError(RuntimeError):
    """事件总线基础异常。"""


class ThreadSessionBindingError(EventBusError):
    """`thread_id` 被尝试绑定到错误 `session_id` 时抛出。"""


class UnknownThreadRegistrationError(EventBusError):
    """未注册 `thread_id` 却尝试发布事件时抛出。"""


class SessionSubscriptionClosed(EventBusError):
    """订阅连接被关闭后，继续消费事件时抛出。"""


@dataclass(slots=True)
class _BufferedEvent:
    """保存进入缓冲窗口的一条事件及其事件时间。"""

    created_at: float
    payload: dict[str, Any]


class SessionSubscription:
    """代表一个 session 的单条 SSE 订阅连接。

    做什么：
        保存该连接待消费的回放事件与实时广播队列，并在连接关闭时通知事件总线释放订阅者。
    关键参数：
        session_id: 当前订阅所属的 session。
        replay_events: 建连时需要优先回放的缓冲事件。
        queue_limit: 实时事件队列容量上限；超过后由总线按 ADR-001 关闭连接。
    返回值：
        调用 `next_event()` 时返回一条可直接用于 SSE 输出的事件字典。
    """

    def __init__(
        self,
        *,
        session_id: str,
        replay_events: list[dict[str, Any]],
        queue_limit: int,
        on_close: Callable[["SessionSubscription"], None],
    ) -> None:
        self.session_id = session_id
        self._replay_events: deque[dict[str, Any]] = deque(replay_events)
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_limit)
        self._closed_event = asyncio.Event()
        self._on_close = on_close
        self._closed = False
        self.close_reason: str | None = None

    @property
    def queue_limit(self) -> int:
        return self._queue.maxsize

    @property
    def is_closed(self) -> bool:
        return self._closed

    def enqueue_live_event(self, event: dict[str, Any]) -> bool:
        """向实时队列写入一条事件；队列已满时返回 `False`。

        为什么这样做：
            ADR-001 明确要求慢消费者在队列满 100 条时立即断开，不允许静默丢弃事件。
        """

        if self._closed:
            return False
        if self._queue.full():
            return False
        self._queue.put_nowait(event)
        return True

    def close(self, reason: str) -> None:
        """关闭订阅并通知事件总线移除当前订阅者。"""

        if self._closed:
            return
        self._closed = True
        self.close_reason = reason
        self._closed_event.set()
        self._on_close(self)

    async def next_event(self) -> dict[str, Any]:
        """返回下一条待发送事件；连接关闭时抛出 `SessionSubscriptionClosed`。"""

        if self._replay_events:
            return self._replay_events.popleft()
        if self._closed:
            raise SessionSubscriptionClosed(self.close_reason or "subscription_closed")

        queue_task = asyncio.create_task(self._queue.get())
        close_task = asyncio.create_task(self._closed_event.wait())
        done, pending = await asyncio.wait({queue_task, close_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()

        if close_task in done and self._closed:
            queue_task.cancel()
            raise SessionSubscriptionClosed(self.close_reason or "subscription_closed")
        return queue_task.result()

    async def iter_events(self):
        """持续产出订阅事件，供后续 SSE 生成器直接复用。"""

        while True:
            yield await self.next_event()


class SessionEventBus:
    """ADR-001 规定的 session 级事件总线。

    做什么：
        统一维护 thread/session 索引、thread 缓冲、session 订阅者以及线程安全发布入口。
    关键参数：
        loop: 主 asyncio 事件循环；工作线程通过 `call_soon_threadsafe` 把发布操作投递回该循环。
        time_fn: 返回单调时间的可注入函数，便于测试清理窗口。
    返回值：
        对外提供 `register_thread()`、`publish_threadsafe()`、`subscribe()` 与 `cleanup_expired()`。
    """

    def __init__(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        time_fn: Callable[[], float] | None = None,
        logger: logging.Logger | None = None,
        buffer_window_seconds: float = DEFAULT_BUFFER_WINDOW_SECONDS,
        buffer_event_limit: int = DEFAULT_BUFFER_EVENT_LIMIT,
        subscriber_queue_limit: int = DEFAULT_SUBSCRIBER_QUEUE_LIMIT,
    ) -> None:
        self._loop = loop
        self._time_fn = time_fn or time.monotonic
        self._logger = logger or logging.getLogger("job_assistant.event_bus")
        self._buffer_window_seconds = buffer_window_seconds
        self._buffer_event_limit = buffer_event_limit
        self._subscriber_queue_limit = subscriber_queue_limit

        self._thread_to_session: dict[str, str] = {}
        self._session_to_threads: dict[str, set[str]] = defaultdict(set)
        self._thread_buffers: dict[str, deque[_BufferedEvent]] = {}
        self._session_subscribers: dict[str, set[SessionSubscription]] = defaultdict(set)
        self._session_sequences: dict[str, int] = defaultdict(int)
        self._terminal_threads: set[str] = set()

    def register_thread(self, session_id: str, thread_id: str) -> None:
        """注册 thread 与 session 的归属关系，禁止后续静默改绑。"""

        existing_session = self._thread_to_session.get(thread_id)
        if existing_session is not None and existing_session != session_id:
            raise ThreadSessionBindingError(f"thread {thread_id} is already bound to session {existing_session}")
        if existing_session is None:
            self._thread_to_session[thread_id] = session_id
            self._session_to_threads[session_id].add(thread_id)
            self._thread_buffers.setdefault(thread_id, deque())
        self.cleanup_expired()

    def publish_threadsafe(self, event: dict[str, Any]) -> None:
        """供工作线程调用的线程安全发布入口。"""

        self._loop.call_soon_threadsafe(self.publish, dict(event))

    def publish(self, event: dict[str, Any]) -> dict[str, Any]:
        """在主事件循环中发布事件，更新缓冲并广播给 session 订阅者。"""

        thread_id = str(event.get("thread_id", ""))
        if not thread_id:
            raise UnknownThreadRegistrationError("event is missing thread_id")
        session_id = self._thread_to_session.get(thread_id)
        if session_id is None:
            raise UnknownThreadRegistrationError(f"thread {thread_id} is not registered")

        claimed_session = event.get("session_id")
        if claimed_session is not None and str(claimed_session) != session_id:
            raise ThreadSessionBindingError(
                f"thread {thread_id} is bound to session {session_id}, not {claimed_session}"
            )

        published = dict(event)
        published["session_id"] = session_id
        published["thread_id"] = thread_id
        published["session_sequence"] = self._next_session_sequence(session_id)
        created_at = self._time_fn()

        buffer = self._thread_buffers.setdefault(thread_id, deque())
        buffer.append(_BufferedEvent(created_at=created_at, payload=published))
        self._prune_thread_buffer(thread_id, now=created_at)

        if str(published.get("event")) in _TERMINAL_EVENTS:
            self._terminal_threads.add(thread_id)

        closed_subscribers: list[SessionSubscription] = []
        for subscriber in list(self._session_subscribers.get(session_id, set())):
            if not subscriber.enqueue_live_event(dict(published)):
                closed_subscribers.append(subscriber)

        for subscriber in closed_subscribers:
            self._logger.warning(
                {
                    "event": "sse_subscriber_disconnected",
                    "session_id": session_id,
                    "thread_id": thread_id,
                    "reason": "slow_consumer",
                    "queue_limit": self._subscriber_queue_limit,
                }
            )
            subscriber.close("slow_consumer")

        self.cleanup_expired(now=created_at)
        return published

    def subscribe(self, session_id: str) -> SessionSubscription:
        """创建一个新的 session 订阅，并优先回放当前窗口内的缓冲事件。"""

        self.cleanup_expired()
        replay_events: list[dict[str, Any]] = []
        for thread_id in sorted(self._session_to_threads.get(session_id, set())):
            replay_events.extend(item.payload for item in self._thread_buffers.get(thread_id, ()))
        replay_events.sort(key=lambda item: int(item.get("session_sequence", 0)))

        subscriber = SessionSubscription(
            session_id=session_id,
            replay_events=replay_events,
            queue_limit=self._subscriber_queue_limit,
            on_close=lambda current: self._remove_subscriber(session_id, current),
        )
        self._session_subscribers[session_id].add(subscriber)
        return subscriber

    def cleanup_expired(self, *, now: float | None = None) -> None:
        """清理超过保留窗口的终态 thread 缓冲与索引。"""

        current = self._time_fn() if now is None else now
        for thread_id, buffer in list(self._thread_buffers.items()):
            self._prune_thread_buffer(thread_id, now=current)
            if thread_id not in self._terminal_threads:
                continue
            buffer = self._thread_buffers.get(thread_id, deque())
            if not buffer:
                self._drop_thread(thread_id)
                continue
            last_event_age = current - buffer[-1].created_at
            if last_event_age > self._buffer_window_seconds:
                self._drop_thread(thread_id)

    def active_threads_for_session(self, session_id: str) -> set[str]:
        """返回当前 session 仍在索引中的全部 thread。"""

        return set(self._session_to_threads.get(session_id, set()))

    def subscriber_count(self, session_id: str) -> int:
        """返回某个 session 当前活跃订阅者数量。"""

        return len(self._session_subscribers.get(session_id, set()))

    def is_thread_registered(self, thread_id: str) -> bool:
        return thread_id in self._thread_to_session

    def is_thread_terminal(self, thread_id: str) -> bool:
        return thread_id in self._terminal_threads

    def buffered_event_count(self, thread_id: str) -> int:
        return len(self._thread_buffers.get(thread_id, ()))

    def _next_session_sequence(self, session_id: str) -> int:
        self._session_sequences[session_id] += 1
        return self._session_sequences[session_id]

    def _prune_thread_buffer(self, thread_id: str, *, now: float) -> None:
        buffer = self._thread_buffers.setdefault(thread_id, deque())
        while buffer and (now - buffer[0].created_at) > self._buffer_window_seconds:
            buffer.popleft()
        while len(buffer) > self._buffer_event_limit:
            buffer.popleft()

    def _drop_thread(self, thread_id: str) -> None:
        session_id = self._thread_to_session.pop(thread_id, None)
        self._thread_buffers.pop(thread_id, None)
        self._terminal_threads.discard(thread_id)
        if session_id is None:
            return
        threads = self._session_to_threads.get(session_id)
        if threads is None:
            return
        threads.discard(thread_id)
        if not threads:
            self._session_to_threads.pop(session_id, None)

    def _remove_subscriber(self, session_id: str, subscriber: SessionSubscription) -> None:
        subscribers = self._session_subscribers.get(session_id)
        if subscribers is None:
            return
        subscribers.discard(subscriber)
        if not subscribers:
            self._session_subscribers.pop(session_id, None)
