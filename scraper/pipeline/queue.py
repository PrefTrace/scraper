from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime

from .models import EnqueueResult, QueueTask, TaskState, TaskSubscriber

TaskHandler = Callable[[QueueTask, "InMemoryTaskQueue"], Awaitable[object | None]]


class InMemoryTaskQueue:
    """Current in-memory queues with logical-task deduplication.

    Queue contents are deliberately not persisted. Source facts and TTL state
    remain the durable coordination layer; a process restart can seed the
    primary queue again without creating duplicate external data.
    """

    def __init__(self, queue_names: Iterable[str]) -> None:
        self._queues = {name: asyncio.Queue[QueueTask]() for name in set(queue_names)}
        self._states: dict[tuple[str, str], TaskState] = {}
        self._lock = asyncio.Lock()
        self._idle = asyncio.Event()
        self._idle.set()
        self._workers: list[asyncio.Task[None]] = []

    async def enqueue(
        self,
        task: QueueTask,
        *,
        subscriber: TaskSubscriber | None = None,
    ) -> EnqueueResult:
        if task.queue not in self._queues:
            raise KeyError(f"Unknown queue: {task.queue}")
        key = (task.queue, task.task_key)
        async with self._lock:
            state = self._states.get(key)
            if state is not None:
                if subscriber is not None:
                    state.subscribers.add(subscriber)
                return EnqueueResult(task=task, created=False, state=state)
            state = TaskState(task=task)
            if subscriber is not None:
                state.subscribers.add(subscriber)
            self._states[key] = state
            self._idle.clear()
            self._queues[task.queue].put_nowait(task)
            return EnqueueResult(task=task, created=True, state=state)

    async def claim(self, task: QueueTask) -> TaskState:
        async with self._lock:
            state = self._states[(task.queue, task.task_key)]
            state.status = "running"
            return state

    async def complete(
        self,
        task: QueueTask,
        *,
        result: object | None = None,
        error: Exception | None = None,
    ) -> None:
        async with self._lock:
            state = self._states[(task.queue, task.task_key)]
            state.status = "failed" if error is not None else "done"
            state.last_error = str(error) if error is not None else None
            state.result = result
            state.updated_at = datetime.now(UTC)
            self._queues[task.queue].task_done()
            if not any(item.qsize() for item in self._queues.values()) and not any(
                item.status == "running" for item in self._states.values()
            ):
                self._idle.set()

    async def get_state(self, queue: str, task_key: str) -> TaskState | None:
        async with self._lock:
            return self._states.get((queue, task_key))

    def has_queue(self, queue: str) -> bool:
        return queue in self._queues

    async def start_worker(self, queue: str, handler: TaskHandler) -> None:
        if queue not in self._queues:
            raise KeyError(f"Unknown queue: {queue}")
        self._workers.append(asyncio.create_task(self._worker(queue, handler)))

    async def _worker(self, queue: str, handler: TaskHandler) -> None:
        source_queue = self._queues[queue]
        while True:
            task = await source_queue.get()
            await self.claim(task)
            try:
                result = await handler(task, self)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.complete(task, error=exc)
            else:
                await self.complete(task, result=result)

    async def wait_idle(self) -> None:
        await self._idle.wait()

    async def stop(self) -> None:
        for worker in self._workers:
            worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    async def states(self) -> list[TaskState]:
        async with self._lock:
            return list(self._states.values())


__all__ = ["InMemoryTaskQueue", "TaskHandler"]
