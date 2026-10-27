"""Subprocess fixture that holds a real PostgreSQL execution lease until SIGKILL."""

import asyncio
import json
import os
import sys
from pathlib import Path

from pydantic import SecretStr
from rollforge_hub_sdk.client import HubClient
from rollforge_scheduler.scheduler import Scheduler
from rollforge_worker.executor import Worker, WorkerSettings


class BlockingWorker(Worker):
    async def execute(self, lease, _directory):
        print(
            json.dumps(
                {
                    "event": "lease",
                    "trial_id": str(lease.trial_id),
                    "execution_id": str(lease.execution_id),
                    "worker_id": str(lease.worker_id),
                    "fencing_token": lease.fencing_token,
                }
            ),
            flush=True,
        )
        await asyncio.Event().wait()


class ReportingHub:
    def __init__(self, client):
        self.client = client

    async def claim(self, request):
        return await self.client.claim(request)

    async def recover_expired(self):
        result = await self.client.recover_expired()
        if result.recovered_count:
            print(json.dumps({"event": "recovered", "count": result.recovered_count}), flush=True)
        return result


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
            ReportingHub(hub),
            None,
        )
        scheduler = Scheduler(
            worker,
            ReportingHub(hub),
            concurrency=1,
            poll_seconds=0.1,
            recovery_seconds=1,
            shutdown_seconds=1,
        )
        await scheduler.run(asyncio.Event())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        token = os.environ.get("ROLLFORGE_TEST_WORKER_TOKEN", "")
        message = str(exc).replace(token, "[redacted]") if token else str(exc)
        print(f"{type(exc).__name__}: {message[:200]}", file=sys.stderr, flush=True)
        raise SystemExit(1) from None
