"""Subprocess fixture that holds a real PostgreSQL execution lease until SIGKILL."""

import asyncio
import json
import os
from pathlib import Path
from uuid import UUID

from rollforge_api.execution_service import ExecutionService
from rollforge_worker.executor import Worker, WorkerSettings
from sqlalchemy.ext.asyncio import create_async_engine


class DatabaseHub:
    def __init__(self, service, worker_id):
        self.service = service
        self.worker_id = worker_id

    async def claim(self, request):
        return await self.service.claim(
            self.worker_id, lease_seconds=1, runnable_only=request.runnable_only
        )


class BlockingWorker(Worker):
    async def execute(self, lease, _directory):
        print(
            json.dumps(
                {
                    "trial_id": str(lease.trial_id),
                    "execution_id": str(lease.execution_id),
                    "worker_id": str(lease.worker_id),
                    "fencing_token": lease.fencing_token,
                }
            ),
            flush=True,
        )
        await asyncio.Event().wait()


async def main():
    url = os.environ["ROLLFORGE_TEST_DATABASE_URL"]
    schema = os.environ["ROLLFORGE_TEST_SCHEMA"]
    worker_id = UUID(os.environ["ROLLFORGE_TEST_WORKER_ID"])
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    try:
        service = ExecutionService(engine)
        worker = BlockingWorker(
            WorkerSettings(
                worker_runs_enabled=True,
                worker_spool=Path(os.environ["ROLLFORGE_TEST_SPOOL"]),
                worker_gateway_url="https://gateway.invalid",
            ),
            DatabaseHub(service, worker_id),
            None,
        )
        await worker.once()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
