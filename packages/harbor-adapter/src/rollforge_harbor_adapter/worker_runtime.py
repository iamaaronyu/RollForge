"""固定 Harbor 子进程入口；平台凭证不进入此进程，Harbor 独占 Sandbox 生命周期。"""

import argparse
import asyncio
import json
import os
import signal
from pathlib import Path

from rollforge_schemas.runtime import RuntimeSpec

from rollforge_harbor_adapter.manifest import content_manifest
from rollforge_harbor_adapter.native import await_native_trial, build_trial_config
from rollforge_harbor_adapter.preflight import preflight, runtime_versions


def validated_task(spec: RuntimeSpec):
    from harbor.models.task.task import Task

    task = Task(spec.task_dir)
    if (
        task.config.verifier.environment_mode is None
        or task.config.verifier.environment_mode.value != "separate"
    ):
        raise ValueError("Worker requires a separate verifier task")
    return task


async def execute(spec: RuntimeSpec):
    from harbor.models.trial.result import TrialResult
    from harbor.trial.trial import Trial

    validated_task(spec)
    original = content_manifest(spec.task_dir).digest
    config = build_trial_config(spec, "trial")
    trial = await Trial.create(config)
    async with asyncio.timeout(spec.timeout_sec * 4):
        await await_native_trial(trial)
    directory = spec.output_dir / "trial"
    TrialResult.model_validate_json((directory / "result.json").read_text())
    if content_manifest(spec.task_dir).digest != original:
        raise ValueError("Task changed during execution")
    return directory


async def guarded(spec: RuntimeSpec):
    running = asyncio.current_task()
    loop = asyncio.get_running_loop()
    for event in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(event, running.cancel)
    try:
        return await execute(spec)
    finally:
        for event in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(event)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    try:
        if args.spec.is_symlink() or args.spec.stat().st_mode & 0o077:
            raise ValueError("Runtime spec must be private")
        spec = RuntimeSpec.model_validate_json(args.spec.read_text())
        if spec.agent != "claude-code" or spec.agent_version != "2.1.81":
            raise ValueError("Unsupported worker runtime profile")
        if preflight(spec, os.environ):
            raise ValueError("Runtime preflight failed")
        build_trial_config(spec, "trial")
        validated_task(spec)
        if not args.run:
            print(json.dumps({"runtime": runtime_versions(), "real_execution": False}))
            return 0
        os.environ["ANTHROPIC_BASE_URL"] = spec.model_base_url
        spec.output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        directory = asyncio.run(guarded(spec))
        # 只有 Harbor 原生结果校验通过才产生完成标记。
        marker = spec.output_dir.parent / "native-ready.json"
        marker.write_text(json.dumps({"directory": str(directory)}))
        marker.chmod(0o600)
        return 0
    except (Exception, KeyboardInterrupt, asyncio.CancelledError) as exc:
        print(json.dumps({"status": "runtime_failed", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
