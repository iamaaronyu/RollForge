"""Subprocess fixture that holds a real PostgreSQL execution lease until SIGKILL."""

import asyncio
import json
import os
from pathlib import Path

from pydantic import SecretStr
from rollforge_hub_sdk.client import HubClient
from rollforge_worker.executor import Worker, WorkerSettings


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
    settings = WorkerSettings(
        worker_runs_enabled=True,
        worker_hub_url=os.environ["ROLLFORGE_TEST_HUB_URL"],
        worker_hub_token=SecretStr(os.environ["ROLLFORGE_TEST_WORKER_TOKEN"]),
        worker_spool=Path(os.environ["ROLLFORGE_TEST_SPOOL"]),
        worker_gateway_url="https://gateway.invalid",
    )
    async with HubClient(settings.worker_hub_url, settings.worker_hub_token) as hub:
        worker = BlockingWorker(
            settings,
            hub,
            None,
        )
        await worker.once()


if __name__ == "__main__":
    asyncio.run(main())
