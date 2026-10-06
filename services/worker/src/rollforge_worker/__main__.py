import argparse
import asyncio
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="RollForge worker")
    parser.add_argument("--check", action="store_true", help="Validate scaffold imports and exit")
    parser.add_argument("--run", action="store_true", help="执行一次真实领取与运行")
    parser.add_argument("--resume", type=Path, help="仅恢复指定 Execution 的上传/提交")
    args = parser.parse_args()
    if args.check:
        from rollforge_worker.executor import Worker

        assert Worker
        print("rollforge_worker: imports ready; no execution performed")
        return
    if not args.run:
        parser.error("需要 --run；默认不创建 Sandbox 或调用模型")
    from rollforge_hub_sdk.client import HubClient
    from rollforge_object_store.s3 import S3ObjectStore

    from rollforge_worker.executor import Worker, WorkerSettings

    settings = WorkerSettings()
    if not settings.worker_runs_enabled or not settings.worker_hub_token.get_secret_value():
        parser.error("需要显式启用 Worker 并配置受限凭证")
    if (
        not settings.object_storage_access_key.get_secret_value()
        or not settings.object_storage_secret_key.get_secret_value()
    ):
        parser.error("需要显式配置对象存储凭证")
    if not settings.worker_gateway_url or settings.worker_runtime_env_file is None:
        parser.error("需要已批准的网关和私密 Runtime 会话文件")

    async def execute():
        store = S3ObjectStore(
            settings.object_storage_endpoint,
            settings.object_storage_bucket,
            settings.object_storage_access_key,
            settings.object_storage_secret_key,
            allow_local_http=settings.object_storage_allow_local_http,
        )
        try:
            async with HubClient(settings.worker_hub_url, settings.worker_hub_token) as hub:
                worker = Worker(settings, hub, store)
                return await worker.resume(args.resume) if args.resume else await worker.once()
        finally:
            store.close()

    try:
        print(json.dumps(asyncio.run(execute())))
    except (Exception, KeyboardInterrupt) as exc:
        print(json.dumps({"status": "worker_failed", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
