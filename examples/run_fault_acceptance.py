"""真实 E2B 故障验收；仅显式 --run 执行，原始输出留在忽略目录。"""

import argparse
import asyncio
import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from rollforge_harbor_adapter.manifest import content_manifest
from rollforge_harbor_adapter.native import build_trial_config
from rollforge_harbor_adapter.preflight import preflight, runtime_versions, spec_from_env
from rollforge_harbor_adapter.results import summarize_result

ROOT = Path(__file__).resolve().parents[1]
CASES = (
    "zero_score",
    "verifier_error",
    "verifier_timeout",
    "agent_timeout",
    "agent_error",
    "cancel",
)


async def sandbox_count() -> int:
    from e2b import AsyncSandbox

    paginator = AsyncSandbox.list()
    count = 0
    while paginator.has_next:
        count += len(await paginator.next_items())
    return count


def prepare_task(root: Path, case: str) -> Path:
    # 保持相同目录名和环境内容，以复用已验收的模板；输入副本仍分别计算摘要。
    task = root / "inputs" / case / "hello-task"
    shutil.copytree(ROOT / "examples/tasks/hello-task", task)
    verifier = {
        "zero_score": "mkdir -p /logs/verifier\nprintf '0' > /logs/verifier/reward.txt\n",
        "verifier_error": "exit 42\n",
        "verifier_timeout": "sleep 120\n",
        "cancel": "sleep 120\n",
    }.get(case)
    if verifier is not None:
        (task / "tests/test.sh").write_text("#!/bin/sh\nset -eu\n" + verifier)
    if case == "agent_timeout":
        (task / "instruction.md").write_text("运行 sleep 120，然后完成原始任务。\n")
    return task


async def run_case(root: Path, case: str) -> dict:
    from harbor.models.trial.result import TrialResult
    from harbor.trial.hooks import TrialEvent
    from harbor.trial.trial import Trial

    task = prepare_task(root, case)
    digest = content_manifest(task).digest
    spec = spec_from_env(os.environ, task, root / "trials")
    config = build_trial_config(spec, case + "-" + uuid4().hex)
    if case == "agent_timeout":
        config.agent.override_timeout_sec = 1
    if case == "verifier_timeout":
        config.verifier.override_timeout_sec = 1
    trial = await Trial.create(config)
    started = asyncio.Event()

    async def verification_started(_event):
        started.set()

    async def break_agent(_event):
        # 固定版本已预装并完成 setup 后，注入 CLI exit 42；不替换 Harbor 实现。
        await trial.agent_environment.exec(
            command="mv /usr/local/bin/claude /usr/local/bin/claude.original && "
            "printf '#!/bin/sh\\nexit 42\\n' > /usr/local/bin/claude && "
            "chmod +x /usr/local/bin/claude"
        )

    if case == "cancel":
        trial.add_hook(TrialEvent.VERIFICATION_START, verification_started)
    if case == "agent_error":
        trial.add_hook(TrialEvent.AGENT_START, break_agent)
    running = asyncio.create_task(trial.run())
    cancelled = False
    try:
        async with asyncio.timeout(900):
            if case == "cancel":

                async def cancel_during_verifier():
                    await started.wait()
                    await asyncio.sleep(0.5)
                    running.cancel()

                trigger = asyncio.create_task(cancel_during_verifier())
                try:
                    await running
                finally:
                    trigger.cancel()
                    await asyncio.gather(trigger, return_exceptions=True)
            else:
                await running
    except asyncio.CancelledError:
        if case != "cancel":
            raise
        cancelled = True
    directory = config.trials_dir / config.trial_name
    TrialResult.model_validate_json((directory / "result.json").read_text())
    summary = summarize_result(directory)
    expected = {
        "zero_score": None,
        "verifier_error": "RewardFileNotFoundError",
        "verifier_timeout": "VerifierTimeoutError",
        "agent_timeout": "AgentTimeoutError",
        "agent_error": "NonZeroAgentExitCodeError",
        "cancel": "CancelledError",
    }[case]
    remaining = await sandbox_count()
    passed = (
        summary.exception_type == expected
        and summary.outcome == ("SCORED" if case == "zero_score" else "RUNTIME_FAILED")
        and (case != "zero_score" or summary.rewards == {"reward": 0.0})
        and (case != "cancel" or cancelled)
        and remaining == 0
        and content_manifest(task).digest == digest
    )
    evidence = {
        "case": case,
        "passed": passed,
        "task_digest": digest,
        "summary": summary.model_dump(mode="json"),
        "cancel_propagated": cancelled,
        "sandboxes_remaining": remaining,
        "output_manifest": content_manifest(directory).model_dump(mode="json"),
    }
    (directory / "rollforge-evidence.json").write_text(json.dumps(evidence, indent=2))
    print(
        json.dumps(
            {
                key: evidence[key]
                for key in ("case", "passed", "sandboxes_remaining", "cancel_propagated")
            }
        ),
        flush=True,
    )
    return evidence


async def run(root: Path) -> int:
    if await sandbox_count():
        raise RuntimeError("需要独占且无活动 Sandbox 的测试环境")
    results = []
    for case in CASES:
        results.append(await run_case(root, case))
        if results[-1]["sandboxes_remaining"]:
            break  # 清理失败后停止创建新 Sandbox。
    report = {"runtime": runtime_versions(), "cases": results}
    (root / "acceptance.json").write_text(json.dumps(report, indent=2))
    return 0 if len(results) == len(CASES) and all(r["passed"] for r in results) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.spike")
    args = parser.parse_args()
    if not args.run:
        print("请显式传入 --run，在独占的真实 E2B 测试环境执行六个故障场景。")
        return 0
    load_dotenv(args.env_file, override=False)
    spec = spec_from_env(os.environ, ROOT / "examples/tasks/hello-task", ROOT / "outputs/spike")
    errors = preflight(spec, os.environ)
    if errors:
        print(json.dumps({"preflight_errors": errors}))
        return 2
    os.environ["ANTHROPIC_BASE_URL"] = spec.model_base_url
    os.umask(0o077)
    root = ROOT / "outputs/spike" / ("faults-" + uuid4().hex)
    root.mkdir(parents=True, mode=0o700)
    try:
        return asyncio.run(run(root))
    except Exception as exc:
        print(json.dumps({"error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
