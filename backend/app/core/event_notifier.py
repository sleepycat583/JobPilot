"""
事件通知机制 - 用于实时推送 SSE 事件，消除轮询延迟
"""
import asyncio
from typing import Dict, Tuple
from threading import Lock


class EventNotifier:
    """
    全局事件通知器，负责在新事件写入数据库时立即通知对应的 SSE 连接

    使用 asyncio.Condition 实现多订阅者的事件通知：
    - 每个 (stream_type, stream_id) 对应一个 Condition
    - append_event 写入后通过 notify_all() 唤醒所有等待者
    - SSE 端点通过 wait() 阻塞，被唤醒后立即读取新事件
    """

    def __init__(self):
        self._conditions: Dict[Tuple[str, str], asyncio.Condition] = {}
        self._lock = Lock()  # 保护 _conditions 字典的线程安全
        self._event_loop: asyncio.AbstractEventLoop | None = None

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        """设置事件循环，必须在应用启动时调用"""
        self._event_loop = loop

    def _get_condition(self, stream_type: str, stream_id: str) -> asyncio.Condition:
        """获取或创建对应流的 Condition 对象"""
        key = (stream_type, stream_id)
        with self._lock:
            if key not in self._conditions:
                self._conditions[key] = asyncio.Condition()
            return self._conditions[key]

    def notify_new_event(self, stream_type: str, stream_id: str):
        """
        通知有新事件写入（从同步上下文调用，如 append_event）

        Args:
            stream_type: 流类型（如 "thread"）
            stream_id: 流 ID（如 thread_id）
        """
        if not self._event_loop:
            return  # 事件循环未初始化，跳过通知

        condition = self._get_condition(stream_type, stream_id)

        # 从同步上下文安全地调度异步通知
        asyncio.run_coroutine_threadsafe(
            self._async_notify(condition),
            self._event_loop
        )

    async def _async_notify(self, condition: asyncio.Condition):
        """异步通知所有等待者"""
        async with condition:
            condition.notify_all()

    async def wait_for_new_event(
        self,
        stream_type: str,
        stream_id: str,
        timeout: float = 0.5
    ) -> bool:
        """
        等待新事件通知（从异步上下文调用，如 SSE 端点）

        Args:
            stream_type: 流类型
            stream_id: 流 ID
            timeout: 超时时间（秒），避免永久阻塞

        Returns:
            True 表示收到通知，False 表示超时
        """
        condition = self._get_condition(stream_type, stream_id)

        try:
            async with condition:
                await asyncio.wait_for(condition.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def cleanup_stream(self, stream_type: str, stream_id: str):
        """清理不再使用的流的 Condition 对象（可选的资源释放）"""
        key = (stream_type, stream_id)
        with self._lock:
            self._conditions.pop(key, None)


# 全局单例
_notifier = EventNotifier()


def get_event_notifier() -> EventNotifier:
    """获取全局事件通知器实例"""
    return _notifier
