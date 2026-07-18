"""SSE 事件总线测试。"""

from __future__ import annotations

import asyncio
import logging
import threading

import pytest

from app.services.event_bus import (
    SessionEventBus,
    SessionSubscriptionClosed,
    ThreadSessionBindingError,
    UnknownThreadRegistrationError,
)


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_subscribe_before_thread_registration_receives_future_event() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        bus = SessionEventBus(loop=asyncio.get_running_loop(), time_fn=clock)
        subscription = bus.subscribe("session-1")

        bus.register_thread("session-1", "thread-1")
        thread = threading.Thread(
            target=bus.publish_threadsafe,
            args=({"event": "node_started", "thread_id": "thread-1", "session_id": "session-1", "node": "supervisor"},),
        )
        thread.start()
        thread.join()

        event = await asyncio.wait_for(subscription.next_event(), timeout=1)

        assert event["event"] == "node_started"
        assert event["thread_id"] == "thread-1"
        assert event["session_id"] == "session-1"

    asyncio.run(scenario())


def test_completed_thread_replays_buffered_events_for_late_subscription() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        bus = SessionEventBus(loop=asyncio.get_running_loop(), time_fn=clock)
        bus.register_thread("session-1", "thread-1")

        bus.publish({"event": "node_started", "thread_id": "thread-1", "session_id": "session-1", "node": "jd_parser"})
        bus.publish({"event": "run_completed", "thread_id": "thread-1", "session_id": "session-1", "final_output": {"type": "jd_parsed"}})

        subscription = bus.subscribe("session-1")
        first = await subscription.next_event()
        second = await subscription.next_event()

        assert [first["event"], second["event"]] == ["node_started", "run_completed"]
        assert [first["session_sequence"], second["session_sequence"]] == [1, 2]

    asyncio.run(scenario())


def test_single_thread_event_order_is_preserved_under_multi_thread_interleaving() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        bus = SessionEventBus(loop=asyncio.get_running_loop(), time_fn=clock)
        bus.register_thread("session-1", "thread-a")
        bus.register_thread("session-1", "thread-b")
        subscription = bus.subscribe("session-1")

        bus.publish({"event": "node_started", "thread_id": "thread-a", "session_id": "session-1", "node": "a-1"})
        bus.publish({"event": "node_started", "thread_id": "thread-b", "session_id": "session-1", "node": "b-1"})
        bus.publish({"event": "node_finished", "thread_id": "thread-a", "session_id": "session-1", "node": "a-2"})

        events = [await subscription.next_event() for _ in range(3)]
        thread_a_events = [event for event in events if event["thread_id"] == "thread-a"]

        assert [event["event"] for event in thread_a_events] == ["node_started", "node_finished"]
        assert [event["session_sequence"] for event in thread_a_events] == [1, 3]

    asyncio.run(scenario())


def test_terminal_thread_cleanup_waits_for_delay_window() -> None:
    clock = FakeClock()
    loop = asyncio.new_event_loop()
    try:
        bus = SessionEventBus(loop=loop, time_fn=clock)
        bus.register_thread("session-1", "thread-1")
        bus.publish({"event": "run_completed", "thread_id": "thread-1", "session_id": "session-1", "final_output": {"type": "jd_parsed"}})

        clock.advance(59)
        bus.cleanup_expired()
        assert bus.is_thread_registered("thread-1") is True

        clock.advance(2)
        bus.cleanup_expired()
        assert bus.is_thread_registered("thread-1") is False
    finally:
        loop.close()


def test_session_subscription_stays_open_when_only_part_of_threads_complete() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        bus = SessionEventBus(loop=asyncio.get_running_loop(), time_fn=clock)
        bus.register_thread("session-1", "thread-1")
        bus.register_thread("session-1", "thread-2")
        subscription = bus.subscribe("session-1")

        bus.publish({"event": "run_completed", "thread_id": "thread-1", "session_id": "session-1", "final_output": {"type": "jd_parsed"}})
        first = await subscription.next_event()
        assert first["thread_id"] == "thread-1"
        assert subscription.is_closed is False

        bus.publish({"event": "node_started", "thread_id": "thread-2", "session_id": "session-1", "node": "resume_matcher"})
        second = await subscription.next_event()
        assert second["thread_id"] == "thread-2"
        assert subscription.is_closed is False

    asyncio.run(scenario())


def test_slow_subscriber_is_disconnected_when_queue_reaches_limit(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    loop = asyncio.new_event_loop()
    try:
        clock = FakeClock()
        logger = logging.getLogger("test.event_bus.slow_consumer")
        logger.handlers = []
        logger.propagate = True
        logger.setLevel(logging.WARNING)
        bus = SessionEventBus(loop=loop, time_fn=clock, logger=logger)
        bus.register_thread("session-1", "thread-1")
        subscription = bus.subscribe("session-1")

        for index in range(101):
            bus.publish({"event": f"event-{index}", "thread_id": "thread-1", "session_id": "session-1"})

        assert subscription.is_closed is True
        assert subscription.close_reason == "slow_consumer"
        assert bus.subscriber_count("session-1") == 0
        assert any(
            isinstance(record.msg, dict) and record.msg.get("event") == "sse_subscriber_disconnected"
            for record in caplog.records
        )
    finally:
        loop.close()


def test_disconnect_removes_subscriber_without_affecting_registered_thread() -> None:
    loop = asyncio.new_event_loop()
    try:
        clock = FakeClock()
        bus = SessionEventBus(loop=loop, time_fn=clock)
        bus.register_thread("session-1", "thread-1")
        subscription = bus.subscribe("session-1")

        subscription.close("client_disconnect")

        assert bus.subscriber_count("session-1") == 0
        assert bus.is_thread_registered("thread-1") is True
    finally:
        loop.close()


def test_thread_binding_cannot_be_forged_or_rebound_to_another_session() -> None:
    loop = asyncio.new_event_loop()
    try:
        clock = FakeClock()
        bus = SessionEventBus(loop=loop, time_fn=clock)
        bus.register_thread("session-1", "thread-1")

        with pytest.raises(ThreadSessionBindingError):
            bus.register_thread("session-2", "thread-1")

        with pytest.raises(ThreadSessionBindingError):
            bus.publish({"event": "node_started", "thread_id": "thread-1", "session_id": "session-2"})

        with pytest.raises(UnknownThreadRegistrationError):
            bus.publish({"event": "node_started", "thread_id": "thread-unknown", "session_id": "session-1"})
    finally:
        loop.close()


def test_terminal_thread_is_removed_when_buffer_naturally_expires_to_empty() -> None:
    loop = asyncio.new_event_loop()
    try:
        clock = FakeClock()
        bus = SessionEventBus(loop=loop, time_fn=clock)
        bus.register_thread("session-1", "thread-1")
        bus.publish({"event": "run_failed", "thread_id": "thread-1", "session_id": "session-1"})

        clock.advance(61)
        bus.cleanup_expired()

        assert bus.is_thread_registered("thread-1") is False
        assert bus.active_threads_for_session("session-1") == set()
    finally:
        loop.close()


def test_closed_subscription_raises_closed_error_on_next_event() -> None:
    async def scenario() -> None:
        bus = SessionEventBus(loop=asyncio.get_running_loop(), time_fn=FakeClock())
        subscription = bus.subscribe("session-1")
        subscription.close("client_disconnect")

        with pytest.raises(SessionSubscriptionClosed):
            await subscription.next_event()

    asyncio.run(scenario())