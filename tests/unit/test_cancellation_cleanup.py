import asyncio

import pytest
from rollforge_harbor_adapter.native import await_native_trial


async def test_repeated_caller_cancel_waits_for_cleanup_and_forwards_once():
    started = asyncio.Event()
    recovering = asyncio.Event()
    release = asyncio.Event()
    cancellations = 0
    cleaned = False

    class ControlledTrial:
        async def run(self):
            nonlocal cancellations, cleaned
            started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancellations += 1
                raise
            finally:
                recovering.set()
                await release.wait()
                cleaned = True

    caller = asyncio.create_task(await_native_trial(ControlledTrial()))
    try:
        await started.wait()
        caller.cancel()
        await recovering.wait()
        for _ in range(2):
            caller.cancel()
            await asyncio.sleep(0)
        assert not caller.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert cancellations == 1
        assert cleaned
    finally:
        release.set()
        caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)


async def test_cleanup_error_is_observed_as_cancellation_cause():
    started = asyncio.Event()

    class FailingCleanup:
        async def run(self):
            started.set()
            try:
                await asyncio.Future()
            finally:
                raise RuntimeError("synthetic cleanup failure")

    caller = asyncio.create_task(await_native_trial(FailingCleanup()))
    await started.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError) as error:
        await caller
    assert isinstance(error.value.__cause__, RuntimeError)
