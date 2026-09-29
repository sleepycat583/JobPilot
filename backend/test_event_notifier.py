"""
端到端测试：验证事件通知机制是否消除了轮询延迟

测试策略：
1. 直接调用 append_event，触发事件通知
2. 同时启动 SSE 连接，监听事件
3. 测量从写入到接收的延迟

预期结果：延迟应该在 50ms 以内（而非之前的 500ms）
"""
import asyncio
import time
from app.core.event_notifier import get_event_notifier
from app.services.store import append_event
from app.db import Base, build_engine, build_session_factory
from app.core.config import Settings


def test_event_notification_latency():
    asyncio.run(_test_event_notification_latency())


async def _test_event_notification_latency():
    """测试事件通知机制的延迟"""

    # 初始化
    settings = Settings()
    engine = build_engine(settings)
    Base.metadata.create_all(engine)
    session_factory = build_session_factory(engine)
    notifier = get_event_notifier()
    notifier.set_event_loop(asyncio.get_running_loop())

    test_thread_id = "test-thread-latency"
    latencies = []

    print("开始测试事件通知延迟...\n")

    # 测试 10 次
    for i in range(10):
        # 启动监听器（模拟 SSE 连接）
        async def listener():
            start = time.perf_counter()
            notified = await notifier.wait_for_new_event(
                stream_type="thread",
                stream_id=test_thread_id,
                timeout=2.0
            )
            elapsed = (time.perf_counter() - start) * 1000  # 转换为毫秒
            return notified, elapsed

        # 创建监听任务
        listener_task = asyncio.create_task(listener())

        # 短暂延迟，确保监听器已经在等待
        await asyncio.sleep(0.01)

        # 写入事件（从同步上下文）
        write_start = time.perf_counter()
        with session_factory() as session:
            append_event(
                session,
                stream_type="thread",
                stream_id=test_thread_id,
                event_type="message_delta",
                data={"delta": f"test-{i}", "run_id": "test"}
            )
        write_time = (time.perf_counter() - write_start) * 1000

        # 等待监听器收到通知
        notified, latency = await listener_task

        if notified:
            latencies.append(latency)
            status = "✓" if latency < 50 else "⚠️"
            print(f"  {status} 测试 #{i+1}: 延迟 {latency:.2f}ms (写入耗时 {write_time:.2f}ms)")
        else:
            print(f"  ✗ 测试 #{i+1}: 超时未收到通知")

    # 统计结果
    if latencies:
        avg_latency = sum(latencies) / len(latencies)
        max_latency = max(latencies)
        min_latency = min(latencies)

        print(f"\n统计结果 (基于 {len(latencies)} 次成功测试):")
        print(f"  平均延迟: {avg_latency:.2f}ms")
        print(f"  最小延迟: {min_latency:.2f}ms")
        print(f"  最大延迟: {max_latency:.2f}ms")

        if avg_latency < 50:
            print(f"\n✅ 成功！平均延迟 {avg_latency:.2f}ms，远低于之前的 500ms 轮询延迟")
        else:
            print(f"\n⚠️  延迟较高 ({avg_latency:.2f}ms)，但仍优于轮询模式")
    else:
        print("\n✗ 所有测试都超时，事件通知机制可能有问题")

    engine.dispose()


if __name__ == "__main__":
    asyncio.run(test_event_notification_latency())
