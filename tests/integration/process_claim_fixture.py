"""Subprocess fixture that holds a real PostgreSQL execution lease until SIGKILL."""

import asyncio
import json
import os
from uuid import UUID

from rollforge_api.execution_service import ExecutionService
from sqlalchemy.ext.asyncio import create_async_engine


async def main():
    url = os.environ["ROLLFORGE_TEST_DATABASE_URL"]
    schema = os.environ["ROLLFORGE_TEST_SCHEMA"]
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    try:
        lease = await ExecutionService(engine).claim(
            UUID(os.environ["ROLLFORGE_TEST_WORKER_ID"]), lease_seconds=1
        )
        if lease is None:
            raise RuntimeError("expected queued test Trial")
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
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
