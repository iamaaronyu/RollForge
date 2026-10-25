"""通过 Hub 创建并验收 Registry 驱动的真实多 Trial Job；默认不执行。"""

import argparse
import asyncio
import json
import os
import stat
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from dotenv import dotenv_values
from pydantic import SecretStr
from rollforge_hub_sdk.client import HubClient
from rollforge_schemas.api import RegistryJobCreateRequest
from rollforge_schemas.domain import RevisionRef, TrialStatus
from rollforge_schemas.execution import (
    ExecutionList,
    ExecutionStatus,
    JobView,
    RegistrySelection,
    TrialList,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "outputs/acceptance"


def evaluate(
    job: JobView,
    trials: TrialList,
    executions: ExecutionList,
    expected_selection: RegistrySelection | None = None,
) -> dict:
    ordered_trials = sorted(trials.items, key=lambda item: item.attempt_index)
    ordered_executions = sorted(
        executions.items, key=lambda item: (item.attempt_index, item.execution_sequence)
    )
    trial_indices = [item.attempt_index for item in ordered_trials]
    execution_ids = [item.execution_id for item in ordered_executions]
    manifest_keys = [
        item.result.manifest_key for item in ordered_executions if item.result is not None
    ]
    registry_matches = job.snapshot.registry is not None and (
        expected_selection is None or job.snapshot.registry == expected_selection
    )
    passed = (
        job.snapshot.runtime is not None
        and registry_matches
        and job.trial_count >= 2
        and job.terminal_trial_count == job.trial_count
        and len(ordered_trials) == job.trial_count
        and trial_indices == list(range(job.trial_count))
        and all(item.status == TrialStatus.COMPLETED for item in ordered_trials)
        and all(
            item.result is not None
            and item.result.outcome == "SCORED"
            and item.result.rewards == {"reward": 1.0}
            for item in ordered_trials
        )
        and len(ordered_executions) == job.trial_count
        and [item.attempt_index for item in ordered_executions] == list(range(job.trial_count))
        and all(item.status == ExecutionStatus.COMPLETED for item in ordered_executions)
        and all(
            item.result is not None and item.result.outcome == "SCORED"
            for item in ordered_executions
        )
        and len(set(execution_ids)) == job.trial_count
        and len(set(manifest_keys)) == job.trial_count
    )
    return {
        "passed": passed,
        "registry_matches": registry_matches,
        "trial_count": job.trial_count,
        "terminal_trial_count": job.terminal_trial_count,
        "trial_statuses": [item.status.value for item in ordered_trials],
        "attempt_indexes": trial_indices,
        "execution_count": len(ordered_executions),
        "unique_execution_count": len(set(execution_ids)),
        "unique_manifest_count": len(set(manifest_keys)),
        "rewards": [item.result.rewards if item.result else None for item in ordered_trials],
    }


def safe_api_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    ) or (
        parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )


def save_evidence(job_id: UUID, evidence: dict):
    output = DEFAULT_OUTPUT
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.chmod(0o700)
    target = output / f"multitrial-{job_id}.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    temporary.chmod(0o600)
    temporary.replace(target)


async def fetch_trials(client: HubClient, job_id: UUID, expected: int) -> TrialList:
    items = []
    after = None
    while len(items) < expected:
        page = await client.list_trials(job_id, limit=min(100, expected), after=after)
        items.extend(page.items)
        after = page.next_cursor
        if after is None:
            break
    return TrialList(items=tuple(items[:expected]), next_cursor=None)


async def fetch_executions(client: HubClient, job_id: UUID) -> ExecutionList:
    items = []
    after = 0
    while True:
        page = await client.list_executions(job_id, limit=100, after=after)
        items.extend(page.items)
        if page.next_cursor is None:
            break
        after = page.next_cursor
    return ExecutionList(items=tuple(items), next_cursor=None)


async def run(args, config: dict[str, str]):
    missing = [
        key
        for key in (
            "ROLLFORGE_ACCEPTANCE_API_URL",
            "ROLLFORGE_ACCEPTANCE_USER_TOKEN",
            "ROLLFORGE_ACCEPTANCE_TASK_ASSET_ID",
            "ROLLFORGE_ACCEPTANCE_TASK_REVISION",
            "ROLLFORGE_ACCEPTANCE_TASK_DIGEST",
            "ROLLFORGE_ACCEPTANCE_AGENT_ASSET_ID",
            "ROLLFORGE_ACCEPTANCE_AGENT_REVISION",
            "ROLLFORGE_ACCEPTANCE_AGENT_DIGEST",
            "ROLLFORGE_ACCEPTANCE_MODEL_ASSET_ID",
            "ROLLFORGE_ACCEPTANCE_MODEL_REVISION",
            "ROLLFORGE_ACCEPTANCE_MODEL_DIGEST",
        )
        if not config.get(key)
    ]
    if missing:
        print(json.dumps({"status": "configuration_required", "fields": missing}))
        return 2

    base_url = config["ROLLFORGE_ACCEPTANCE_API_URL"]
    if not safe_api_url(base_url):
        print(
            json.dumps(
                {"status": "configuration_invalid", "fields": ["ROLLFORGE_ACCEPTANCE_API_URL"]}
            )
        )
        return 2

    try:
        selection = RegistrySelection(
            **{
                name: RevisionRef(
                    id=config[f"ROLLFORGE_ACCEPTANCE_{name.upper()}_ASSET_ID"],
                    revision=int(config[f"ROLLFORGE_ACCEPTANCE_{name.upper()}_REVISION"]),
                    digest=config[f"ROLLFORGE_ACCEPTANCE_{name.upper()}_DIGEST"],
                )
                for name in ("task", "agent", "model")
            }
        )
        job_id = UUID(args.job_id) if args.job_id else uuid4()
        trials_count = args.trials
        max_executions = args.max_executions
        wait_seconds = args.wait_seconds
        token = config["ROLLFORGE_ACCEPTANCE_USER_TOKEN"]
    except (ValueError, TypeError):
        print(json.dumps({"status": "configuration_invalid", "fields": ["revision_references"]}))
        return 2

    client = HubClient(base_url, SecretStr(token))
    deadline = time.monotonic() + wait_seconds
    try:
        created = await client.create_job_from_revisions(
            RegistryJobCreateRequest(
                job_id=job_id,
                revisions=selection,
                trial_count=trials_count,
                max_executions=max_executions,
            )
        )
        if created.trial_count != trials_count or created.snapshot.registry != selection:
            raise RuntimeError("job_contract_mismatch")
        while time.monotonic() < deadline:
            job = await client.get_job(job_id)
            trials = await fetch_trials(client, job_id, trials_count)
            if len(trials.items) == trials_count and all(
                item.status in {TrialStatus.COMPLETED, TrialStatus.FAILED, TrialStatus.CANCELLED}
                for item in trials.items
            ):
                executions = await fetch_executions(client, job_id)
                job = await client.get_job(job_id)
                evidence = {
                    "job_id": str(job_id),
                    "registry_bound": job.snapshot.registry == selection,
                    "result": evaluate(job, trials, executions, selection),
                }
                save_evidence(job_id, evidence)
                print(json.dumps(evidence, sort_keys=True))
                return 0 if evidence["result"]["passed"] else 1
            await asyncio.sleep(args.poll_seconds)

        print(json.dumps({"status": "timed_out", "job_id": str(job_id)}))
        return 1
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "hub_or_job_error",
                    "error_type": type(exc).__name__,
                    "job_id": str(job_id),
                }
            )
        )
        return 1
    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="提交 2–100 个真实 Trial 并轮询终态")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.acceptance")
    parser.add_argument("--trials", type=int, default=2)
    parser.add_argument("--max-executions", type=int, default=1)
    parser.add_argument("--wait-seconds", type=int, default=3600)
    parser.add_argument("--poll-seconds", type=float, default=3)
    parser.add_argument("--job-id", help="重试请求时使用原 Job UUID 实现幂等")
    args = parser.parse_args()
    if not args.run:
        print("离线模式，不调用 Hub、Worker、Harbor 或模型；真实运行需显式传入 --run。")
        return 0
    if not 2 <= args.trials <= 100 or not 1 <= args.max_executions <= 3:
        print(
            json.dumps({"status": "configuration_invalid", "fields": ["trials", "max_executions"]})
        )
        return 2
    if not 1 <= args.wait_seconds <= 86400 or not 0.5 <= args.poll_seconds <= 30:
        print(
            json.dumps(
                {"status": "configuration_invalid", "fields": ["wait_seconds", "poll_seconds"]}
            )
        )
        return 2
    try:
        info = args.env_file.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            print(
                json.dumps({"status": "configuration_invalid", "fields": ["env_file_permissions"]})
            )
            return 2
        config = {key: value for key, value in dotenv_values(args.env_file).items() if value}
        os.umask(0o077)
        return asyncio.run(run(args, config))
    except FileNotFoundError:
        print(json.dumps({"status": "configuration_required", "fields": ["env_file"]}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
