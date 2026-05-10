"""
鸿钧 · 请求队列

并发控制：最多 4 个并发请求。
超过的请求进入 FIFO 队列等待。
"""

import asyncio
import threading
import uuid
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable
from concurrent.futures import Future


class TaskPriority(str, Enum):
    HIGH = "high"    # 管理员命令
    NORMAL = "normal"
    LOW = "low"      # 定时任务


@dataclass
class QueuedTask:
    id: str
    session_id: str
    priority: TaskPriority
    created_at: float
    future: Future = field(default_factory=None)
    cancelled: bool = False

    def __lt__(self, other):
        # 优先级队列排序：HIGH > NORMAL > LOW
        order = {TaskPriority.HIGH: 0, TaskPriority.NORMAL: 1, TaskPriority.LOW: 2}
        if self.priority != other.priority:
            return order[self.priority] < order[other.priority]
        return self.created_at < other.created_at


class RequestQueue:
    """
    并发控制队列。

    - max_concurrent: 最大并发数（默认 4）
    - 超过并发上限的请求进入 FIFO 等待队列
    - 支持优先级：HIGH 优先于 NORMAL 优先于 LOW
    """

    def __init__(self, max_concurrent: int = 4):
        self.max_concurrent = max_concurrent
        self._active: dict[str, QueuedTask] = {}  # session_id → task
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._lock = threading.Lock()
        self._semaphore = asyncio.Semaphore(max_concurrent)

    def _sync_active_count(self) -> int:
        with self._lock:
            # 清理已完成的 task
            done = [sid for sid, t in self._active.items() if t.future and t.future.done()]
            for sid in done:
                del self._active[sid]
            return len(self._active)

    def is_full(self) -> bool:
        """检查是否已达到并发上限"""
        return self._sync_active_count() >= self.max_concurrent

    def get_active_count(self) -> int:
        return self._sync_active_count()

    def get_queue_length(self) -> int:
        return self._queue.qsize()

    async def enqueue(
        self,
        session_id: str,
        coro: Awaitable,
        priority: TaskPriority = TaskPriority.NORMAL,
    ) -> tuple[str, Awaitable]:
        """
        将协程加入队列。
        返回 (task_id, 待执行的协程)。
        调用方需要 await 这个协程来执行任务。
        """
        task_id = str(uuid.uuid4())

        async def bounded():
            async with self._semaphore:
                await coro

        # 先加入活跃表（用于追踪）
        task = QueuedTask(
            id=task_id,
            session_id=session_id,
            priority=priority,
            created_at=time.time(),
        )
        with self._lock:
            self._active[session_id] = task

        await self._queue.put((task, bounded))
        return task_id, bounded

    async def dequeue(self) -> tuple[QueuedTask, Awaitable]:
        """从队列取出下一个任务（阻塞直到有任务）"""
        task, bounded_coro = await self._queue.get()
        return task, bounded_coro

    def release(self, session_id: str):
        """手动释放一个 session 的并发槽（用于异常情况）"""
        with self._lock:
            if session_id in self._active:
                del self._active[session_id]
                self._semaphore.release()

    def get_status(self) -> dict:
        return {
            "max_concurrent": self.max_concurrent,
            "active_count": self.get_active_count(),
            "queue_length": self.get_queue_length(),
        }
