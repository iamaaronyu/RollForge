import asyncio

import pytest
from pydantic import ValidationError
from rollforge_scheduler.__main__ import settings_type
from rollforge_scheduler.scheduler import Scheduler


class FakeHub:
    def __init__(self):
        self.recoveries = 0

    async def recover_expired(self):
        self.recoveries += 1
        return type("Recovery", (), {"recovered_count": 0})()


def test_scheduler_defaults_off_and_validates_limits():
    settings = settings_type()(_env_file=None)
    assert settings.scheduler_enabled is False
    assert settings.worker_runs_enabled is False
    with pytest.raises(ValidationError):
        settings_type()(_env_file=None, scheduler_concurrency=17)


async def test_scheduler_respects_concurrency_recovers_and_stops():
    stop = asyncio.Event()

    class FakeWorker:
        active = 0
        maximum = 0
        finished = 0

        async def once(self):
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            try:
                await asyncio.sleep(0.002)
            finally:
                self.active -= 1
            self.finished += 1
            if self.finished >= 8:
                stop.set()
            return {"status": "COMPLETED"}

    worker, hub = FakeWorker(), FakeHub()
    scheduler = Scheduler(worker, hub, concurrency=3, poll_seconds=0.1, recovery_seconds=1)
    await asyncio.wait_for(scheduler.run(stop), timeout=2)
    assert worker.maximum == 3
    assert worker.active == 0 and worker.finished >= 8
    assert hub.recoveries == 1


async def test_scheduler_shutdown_drains_active_workers():
    stop = asyncio.Event()
    release = asyncio.Event()
    cleaned = asyncio.Event()
    started = asyncio.Event()

    class BlockingWorker:
        async def once(self):
            started.set()
            try:
                await release.wait()
            finally:
                cleaned.set()

    scheduler = Scheduler(
        BlockingWorker(),
        FakeHub(),
        concurrency=2,
        poll_seconds=0.1,
        recovery_seconds=1,
        shutdown_seconds=2,
    )
    task = asyncio.create_task(scheduler.run(stop))
    await asyncio.wait_for(started.wait(), timeout=1)
    stop.set()
    await asyncio.sleep(0.01)
    assert not task.done()
    release.set()
    await asyncio.wait_for(task, timeout=1)
    assert cleaned.is_set()


async def test_scheduler_cancels_after_grace_and_awaits_worker_cleanup():
    stop = asyncio.Event()
    started = asyncio.Event()
    cleaned = asyncio.Event()

    class BlockingWorker:
        async def once(self):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

    scheduler = Scheduler(
        BlockingWorker(),
        FakeHub(),
        concurrency=1,
        poll_seconds=0.1,
        recovery_seconds=1,
        shutdown_seconds=1,
    )
    task = asyncio.create_task(scheduler.run(stop))
    await asyncio.wait_for(started.wait(), timeout=1)
    stop.set()
    await asyncio.wait_for(task, timeout=2)
    assert cleaned.is_set()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"concurrency": 0, "poll_seconds": 1, "recovery_seconds": 5},
        {"concurrency": 17, "poll_seconds": 1, "recovery_seconds": 5},
        {"concurrency": 1, "poll_seconds": 0, "recovery_seconds": 5},
        {"concurrency": 1, "poll_seconds": 1, "recovery_seconds": 0},
        {
            "concurrency": 1,
            "poll_seconds": 1,
            "recovery_seconds": 1,
            "shutdown_seconds": 3601,
        },
    ],
)
def test_scheduler_rejects_unsafe_bounds(kwargs):
    with pytest.raises(ValueError):
        Scheduler(object(), object(), **kwargs)
