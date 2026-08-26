from __future__ import annotations

import asyncio
import logging

from app.db.models import init_db
from app.jobs.handler import handle_job
from app.jobs.queue import SqsJobQueue

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    init_db()
    queue = SqsJobQueue()
    logger.info("SQS worker polling %s", queue.queue_url)
    await queue.poll(handle_job)


if __name__ == "__main__":
    asyncio.run(main())
