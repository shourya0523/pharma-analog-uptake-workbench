from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

from app.config import get_settings


JobHandler = Callable[[dict[str, Any]], Awaitable[None]]


class JobQueue(ABC):
    @abstractmethod
    async def enqueue(self, job_type: str, payload: dict[str, Any]) -> str:
        ...

    @abstractmethod
    async def start(self, handler: JobHandler) -> None:
        ...

    @abstractmethod
    async def stop(self) -> None:
        ...


class InProcessJobQueue(JobQueue):
    """MVP / test runner. Swap for SQS in AWS without changing callers."""

    def __init__(self, max_concurrent: int | None = None) -> None:
        settings = get_settings()
        self._sem = asyncio.Semaphore(max_concurrent or settings.max_concurrent_jobs)
        self._handler: JobHandler | None = None
        self._tasks: set[asyncio.Task] = set()
        self._running = False

    async def enqueue(self, job_type: str, payload: dict[str, Any]) -> str:
        if not self._handler:
            raise RuntimeError("JobQueue not started")
        job_id = payload.get("job_id", job_type)

        async def _run() -> None:
            async with self._sem:
                assert self._handler
                await self._handler({"job_type": job_type, **payload})

        task = asyncio.create_task(_run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return str(job_id)

    async def start(self, handler: JobHandler) -> None:
        self._handler = handler
        self._running = True

    async def stop(self) -> None:
        self._running = False
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)


class SqsJobQueue(JobQueue):
    """AWS SQS-backed queue. Workers poll separately in ECS."""

    def __init__(self, queue_url: str | None = None) -> None:
        import boto3

        settings = get_settings()
        self.queue_url = queue_url or settings.sqs_queue_url
        if not self.queue_url:
            raise ValueError("sqs_queue_url required for SqsJobQueue")
        session = boto3.Session(profile_name=settings.aws_profile, region_name=settings.aws_region)
        self.client = session.client("sqs")
        self._handler: JobHandler | None = None

    async def enqueue(self, job_type: str, payload: dict[str, Any]) -> str:
        import json

        body = json.dumps({"job_type": job_type, **payload})
        resp = self.client.send_message(QueueUrl=self.queue_url, MessageBody=body)
        return resp["MessageId"]

    async def start(self, handler: JobHandler) -> None:
        # Polling loop runs in worker process; API process only enqueues.
        self._handler = handler

    async def stop(self) -> None:
        return None


def get_job_queue() -> JobQueue:
    settings = get_settings()
    if settings.job_backend == "sqs":
        return SqsJobQueue()
    return InProcessJobQueue()
