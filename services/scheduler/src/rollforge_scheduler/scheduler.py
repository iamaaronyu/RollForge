"""单进程有界并发 Worker 调度与租约恢复。"""

import asyncio
import logging

logger = logging.getLogger("rollforge.scheduler")


class Scheduler:
    def __init__(
        self,
        worker,
        hub,
        *,
        concurrency: int,
        poll_seconds: float,
        recovery_seconds: float,
        shutdown_seconds: float = 300,
    ):
        if not 1 <= concurrency <= 16:
            raise ValueError("Scheduler concurrency must be 1..16")
        if not 0.1 <= poll_seconds <= 30:
            raise ValueError("Scheduler poll interval must be 0.1..30 seconds")
        if not 1 <= recovery_seconds <= 300:
            raise ValueError("Scheduler recovery interval must be 1..300 seconds")
        if not 1 <= shutdown_seconds <= 3600:
            raise ValueError("Scheduler shutdown grace must be 1..3600 seconds")
        self.worker = worker
        self.hub = hub
        self.concurrency = concurrency
        self.poll_seconds = poll_seconds
        self.recovery_seconds = recovery_seconds
        self.shutdown_seconds = shutdown_seconds

    @staticmethod
    async def _wait(stop: asyncio.Event, seconds: float):
        try:
            await asyncio.wait_for(stop.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def _worker_loop(self, stop: asyncio.Event, slot: int):
        backoff = self.poll_seconds
        while not stop.is_set():
            try:
                result = await self.worker.once()
                if result.get("status") == "no_runnable_trial":
                    await self._wait(stop, self.poll_seconds)
                backoff = self.poll_seconds
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # 不记录原始异常，避免任务输入、凭证或远端响应进入日志。
                logger.warning("worker slot %d failed (%s)", slot, type(exc).__name__)
                await self._wait(stop, backoff)
                backoff = min(backoff * 2, 30)

    async def _recovery_loop(self, stop: asyncio.Event):
        while not stop.is_set():
            try:
                result = await self.hub.recover_expired()
                if result.recovered_count:
                    logger.info("recovered %d expired executions", result.recovered_count)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("lease recovery failed (%s)", type(exc).__name__)
            await self._wait(stop, self.recovery_seconds)

    async def run(self, stop: asyncio.Event):
        tasks = [
            asyncio.create_task(self._worker_loop(stop, slot), name=f"worker-{slot}")
            for slot in range(self.concurrency)
        ]
        tasks.append(asyncio.create_task(self._recovery_loop(stop), name="lease-recovery"))
        try:
            await stop.wait()
        finally:
            _, pending = await asyncio.wait(tasks, timeout=self.shutdown_seconds)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
