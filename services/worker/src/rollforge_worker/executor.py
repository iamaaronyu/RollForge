"""一次领取一个可运行 Trial；子进程隔离 Runtime，续租失败时停止执行。"""

import asyncio
import os
import signal
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import SecretStr
from rollforge_common.settings import Settings
from rollforge_hub_sdk.client import HubClient
from rollforge_object_store.bundle import BundlePublisher
from rollforge_object_store.s3 import S3ObjectStore
from rollforge_schemas.api import ClaimRequest, FinishRequest, LeaseReference, RenewRequest
from rollforge_schemas.execution import Lease
from rollforge_schemas.runtime import RuntimeSpec
from rollforge_schemas.storage import ExecutionScope

from rollforge_worker.tasks import atomic_write, protected_file, unpack_task


class WorkerError(Exception):
    pass


class WorkerSettings(Settings):
    worker_runs_enabled: bool = False
    worker_hub_url: str = "http://127.0.0.1:8000"
    worker_hub_token: SecretStr = SecretStr("")
    worker_runtime_python: Path = Path("integration/harbor-runtime/.venv/bin/python")
    worker_runtime_env_file: Path | None = None
    worker_gateway_url: str = ""
    worker_allow_lan_http: bool = False
    worker_spool: Path = Path("outputs/worker")


def reference(lease: Lease) -> LeaseReference:
    return LeaseReference(
        trial_id=lease.trial_id, execution_id=lease.execution_id, fencing_token=lease.fencing_token
    )


def runtime_environment(settings: WorkerSettings, directory: Path):
    if settings.worker_runtime_env_file is None:
        raise WorkerError("需要私密 Runtime 会话文件")
    protected_file(settings.worker_runtime_env_file)
    values = dotenv_values(settings.worker_runtime_env_file, interpolate=False)
    token = values.get("ROLLFORGE_MODEL_SESSION_TOKEN")
    if not token or not values.get("E2B_API_KEY"):
        raise WorkerError("Runtime 会话或 E2B 凭证未配置")
    home = directory / "runtime-home"
    home.mkdir(mode=0o700, exist_ok=True)
    # 只传入 Runtime 必需的值；Hub/S3 凭证和上游模型 Key 不继承。
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "LANG": "en_US.UTF-8",
        "ANTHROPIC_API_KEY": token,
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "8192",
    }
    for name in ("E2B_API_KEY", "E2B_API_URL", "E2B_SANDBOX_URL", "E2B_DOMAIN"):
        if values.get(name):
            env[name] = values[name]
    return env


class Worker:
    def __init__(self, settings: WorkerSettings, hub: HubClient, objects: S3ObjectStore):
        self.settings, self.hub, self.objects = settings, hub, objects

    async def heartbeat(self, lease: Lease):
        while True:
            await asyncio.sleep(30)
            await self.hub.renew(RenewRequest(lease=reference(lease), lease_seconds=300))

    async def protected(self, lease: Lease, operation):
        heartbeat = asyncio.create_task(self.heartbeat(lease))
        running = asyncio.create_task(operation)
        try:
            done, _ = await asyncio.wait((heartbeat, running), return_when=asyncio.FIRST_COMPLETED)
            if running in done:
                return await running
            await heartbeat
            raise WorkerError("续租任务意外退出")
        finally:
            heartbeat.cancel()
            running.cancel()
            cleanup = asyncio.gather(heartbeat, running, return_exceptions=True)
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue

    async def once(self):
        if not self.settings.worker_runs_enabled:
            raise WorkerError("Worker 真实执行开关未启用")
        lease = await self.hub.claim(ClaimRequest(lease_seconds=300, runnable_only=True))
        if lease is None:
            return {"status": "no_runnable_trial"}
        if self.settings.worker_spool.is_symlink():
            raise WorkerError("Spool 目录不安全")
        root = self.settings.worker_spool.resolve()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root.chmod(0o700)
        directory = root / str(lease.execution_id)
        directory.mkdir(mode=0o700, exist_ok=False)
        atomic_write(directory / "lease.json", lease.model_dump_json())
        return await self.protected(lease, self.execute(lease, directory))

    def check_binding(self, lease: Lease):
        binding = lease.snapshot.runtime
        if binding is None or binding.model_base_url != self.settings.worker_gateway_url.rstrip(
            "/"
        ):
            raise WorkerError("Snapshot 不匹配已批准的网关配置")
        if (
            urlsplit(binding.model_base_url).scheme != "https"
            and not self.settings.worker_allow_lan_http
        ):
            raise WorkerError("Worker 网关需要 HTTPS；LAN 实验需显式启用")
        return binding

    async def execute(self, lease: Lease, directory: Path):
        binding = self.check_binding(lease)
        data = await asyncio.to_thread(self.objects.read, binding.task_archive_key)
        await asyncio.to_thread(
            unpack_task,
            data,
            directory / "task",
            binding.task_archive_digest,
            lease.snapshot.task.digest,
        )
        env = runtime_environment(self.settings, directory)
        spec = RuntimeSpec(
            agent=binding.agent_name,
            protocol=binding.protocol,
            agent_version=binding.agent_version,
            model_name=binding.model_name,
            model_base_url=binding.model_base_url,
            task_dir=directory / "task",
            output_dir=directory / "native",
            timeout_sec=lease.snapshot.timeout_sec,
        )
        atomic_write(directory / "runtime-spec.json", spec.model_dump_json())
        process = await asyncio.create_subprocess_exec(
            str(self.settings.worker_runtime_python.resolve()),
            "-m",
            "rollforge_harbor_adapter.worker_runtime",
            "--spec",
            str(directory / "runtime-spec.json"),
            "--run",
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            code = await process.wait()
        except asyncio.CancelledError:
            if process.returncode is None:
                process.send_signal(signal.SIGTERM)
            cleanup = asyncio.create_task(process.wait())
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
            raise
        if code or not (directory / "native-ready.json").is_file():
            raise WorkerError("Runtime 未生成已验证的原始结果，保留执行以待有界回收")
        return await self.publish(lease, directory, env)

    async def publish(self, lease: Lease, directory: Path, env: dict):
        values = (
            self.settings.worker_hub_token,
            self.settings.object_storage_access_key,
            self.settings.object_storage_secret_key,
            SecretStr(env["ANTHROPIC_API_KEY"]),
            SecretStr(env["E2B_API_KEY"]),
        )
        publisher = BundlePublisher(self.objects, sensitive_values=values)
        scope = ExecutionScope(
            job_id=lease.job_id,
            trial_id=lease.trial_id,
            execution_id=lease.execution_id,
            fencing_token=lease.fencing_token,
        )
        result = await asyncio.to_thread(publisher.publish, scope, directory / "native/trial")
        body = FinishRequest(lease=reference(lease), result=result)
        atomic_write(directory / "pending-commit.json", body.model_dump_json())
        finished = await self.hub.finish(body)
        atomic_write(directory / "committed.json", finished.model_dump_json())
        return {"status": finished.status, "execution_id": str(lease.execution_id)}

    async def resume(self, directory: Path):
        if not self.settings.worker_runs_enabled:
            raise WorkerError("Worker 恢复开关未启用")
        if directory.is_symlink():
            raise WorkerError("Spool 目录不安全")
        directory = directory.resolve()
        protected_file(directory / "lease.json")
        lease = Lease.model_validate_json((directory / "lease.json").read_text())
        self.check_binding(lease)
        pending = directory / "pending-commit.json"
        if pending.is_file():
            protected_file(pending)
            body = FinishRequest.model_validate_json(pending.read_text())
            if body.lease != reference(lease):
                raise WorkerError("Spool 提交身份不匹配")
            finished = await self.hub.finish(body)
            atomic_write(directory / "committed.json", finished.model_dump_json())
            return {"status": finished.status, "execution_id": str(lease.execution_id)}
        if not (directory / "native-ready.json").is_file():
            raise WorkerError("没有可恢复上传的原始结果；不能重跑同一 Execution")
        await self.hub.renew(RenewRequest(lease=reference(lease), lease_seconds=300))
        return await self.protected(
            lease, self.publish(lease, directory, runtime_environment(self.settings, directory))
        )
