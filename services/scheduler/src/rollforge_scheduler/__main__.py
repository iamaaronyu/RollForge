"""RollForge 有界并发 Worker 调度进程。"""

import argparse
import asyncio
import logging
import signal


def settings_type():
    from pydantic import Field
    from rollforge_worker.executor import WorkerSettings

    class Settings(WorkerSettings):
        scheduler_enabled: bool = False
        scheduler_concurrency: int = Field(default=1, ge=1, le=16)
        scheduler_poll_seconds: float = Field(default=1.0, ge=0.1, le=30)
        scheduler_recovery_seconds: int = Field(default=15, ge=1, le=300)
        scheduler_shutdown_seconds: int = Field(default=300, ge=1, le=3600)

    return Settings


async def serve(settings, stop: asyncio.Event):
    from rollforge_hub_sdk.client import HubClient
    from rollforge_object_store.s3 import S3ObjectStore
    from rollforge_worker.executor import Worker

    from rollforge_scheduler.scheduler import Scheduler

    if not settings.scheduler_enabled or not settings.worker_runs_enabled:
        raise ValueError("Scheduler and real Worker execution must both be explicitly enabled")
    if not settings.worker_hub_token.get_secret_value():
        raise ValueError("Worker Hub credential is required")
    if not settings.object_storage_access_key.get_secret_value():
        raise ValueError("Object storage access key is required")
    if not settings.object_storage_secret_key.get_secret_value():
        raise ValueError("Object storage secret key is required")
    if not settings.worker_gateway_url or settings.worker_runtime_env_file is None:
        raise ValueError("Approved model gateway and private Runtime session file are required")

    objects = S3ObjectStore(
        settings.object_storage_endpoint,
        settings.object_storage_bucket,
        settings.object_storage_access_key,
        settings.object_storage_secret_key,
        allow_local_http=settings.object_storage_allow_local_http,
    )
    try:
        async with HubClient(settings.worker_hub_url, settings.worker_hub_token) as hub:
            worker = Worker(settings, hub, objects)
            scheduler = Scheduler(
                worker,
                hub,
                concurrency=settings.scheduler_concurrency,
                poll_seconds=settings.scheduler_poll_seconds,
                recovery_seconds=settings.scheduler_recovery_seconds,
                shutdown_seconds=settings.scheduler_shutdown_seconds,
            )
            await scheduler.run(stop)
    finally:
        objects.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Validate imports without running")
    parser.add_argument("--run", action="store_true", help="Start continuous scheduling")
    parser.add_argument("--concurrency", type=int, help="Override configured in-flight limit")
    args = parser.parse_args()
    if args.check:
        from rollforge_scheduler.scheduler import Scheduler

        assert Scheduler
        print("rollforge_scheduler: imports ready; no execution performed")
        return
    if not args.run:
        parser.error("需要 --run；默认不领取任务或创建 Sandbox")

    Settings = settings_type()
    settings = Settings()
    if args.concurrency is not None:
        settings.scheduler_concurrency = args.concurrency
    stop = asyncio.Event()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        loop.run_until_complete(serve(settings, stop))
    except Exception as exc:
        logging.error("scheduler stopped (%s)", type(exc).__name__)
        raise SystemExit(1) from None
    finally:
        loop.close()


if __name__ == "__main__":
    main()
