"""真实运行边界探测；只输出布尔结果，不读取或记录凭证值。"""

import argparse
import asyncio
import hashlib
import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values, load_dotenv
from harbor.trial.hooks import TrialEvent
from harbor.trial.trial import Trial
from rollforge_harbor_adapter.manifest import content_manifest
from rollforge_harbor_adapter.native import build_trial_config
from rollforge_harbor_adapter.preflight import preflight, runtime_versions, spec_from_env
from rollforge_harbor_adapter.results import summarize_result

ROOT = Path(__file__).resolve().parents[1]


async def run(root: Path, separate_verifier: bool = False) -> int:
    from e2b import AsyncSandbox

    paginator = AsyncSandbox.list()
    if await paginator.next_items():
        raise RuntimeError("需要无活动 Sandbox 的独占测试环境")
    task = root / "inputs/hello-task"
    shutil.copytree(ROOT / "examples/tasks/hello-task", task)
    (task / "tests/private-canary").write_text(uuid4().hex)
    values = dotenv_values(ROOT / ".env.spike")
    credentials = [
        value.encode()
        for name, value in values.items()
        if value and ("KEY" in name or "TOKEN" in name)
    ]
    digests = [hashlib.sha256(value).hexdigest() for value in credentials]
    probe = """import hashlib, json, os
from pathlib import Path
names = ['ANTHROPIC_API_KEY','ANTHROPIC_AUTH_TOKEN','E2B_API_KEY']
flags = {name: bool(os.environ.get(name)) for name in names}
flags['upstream_credential_visible'] = any(
    hashlib.sha256(value.encode()).hexdigest() in EXPECTED_DIGESTS
    for value in os.environ.values()
)
flags['tests_visible'] = Path('/tests/private-canary').exists()
Path('/app/isolation.json').write_text(json.dumps(flags))
"""
    probe = probe.replace("EXPECTED_DIGESTS", repr(digests))
    (task / "instruction.md").write_text(
        "先将以下 Python 代码写入 /app/probe.py 并执行；禁止打印任何凭证值。"
        "然后将 /app/answer.txt 内容写为 RollForge ready 后换行。\n```python\n" + probe + "```\n"
    )
    (task / "task.toml").write_text(
        (task / "task.toml")
        .read_text()
        .replace(
            'artifacts = ["/app/answer.txt"]',
            'artifacts = ["/app/answer.txt", "/app/isolation.json"]',
        )
    )
    with (task / "tests/test.sh").open("a") as f:
        f.write(
            "python - <<'PYPROBE'\n"
            + probe.replace("/app/isolation.json", "/logs/verifier/isolation.json")
            + "PYPROBE\n"
        )
    spec = spec_from_env(os.environ, task, root / "trials")
    if separate_verifier:
        config_text = (task / "task.toml").read_text()
        (task / "task.toml").write_text(
            config_text.replace("[verifier]", '[verifier]\nenvironment_mode = "separate"')
        )
    digest = content_manifest(task).digest
    trial = await Trial.create(build_trial_config(spec, "isolation-" + uuid4().hex))
    boundary = {}

    async def before_agent(_event):
        if spec.model_base_url.startswith("http://"):
            import shlex

            url = shlex.quote(spec.model_base_url + "/v1/messages")
            probe = await trial.agent_environment.exec(
                command="curl --noproxy '*' --max-time 5 -s -o /dev/null "
                "-w '%{http_code}' -X POST "
                + url
                + " -H 'Content-Type: application/json' "
                + '-d \'{"model":"deepseek-flash","max_tokens":10}\''
            )
            boundary["gateway_probe_exit"] = probe.return_code
            boundary["gateway_probe_http_status"] = probe.stdout.strip()
            print(json.dumps({"gateway_probe": boundary}), flush=True)
        result = await trial.agent_environment.exec(command="test ! -e /tests/private-canary")
        boundary["private_tests_absent_before_agent"] = result.return_code == 0

    async def before_verifier(_event):
        if separate_verifier:
            paginator = AsyncSandbox.list()
            count = 0
            while paginator.has_next:
                count += len(await paginator.next_items())
            boundary["agent_sandbox_deleted_before_verifier"] = count == 0

    trial.add_hook(TrialEvent.AGENT_START, before_agent)
    trial.add_hook(TrialEvent.VERIFICATION_START, before_verifier)
    async with asyncio.timeout(900):
        await trial.run()
    directory = trial.config.trials_dir / trial.config.trial_name
    summary = summarize_result(directory)

    def flags_at(path):
        return json.loads(path.read_text()) if path.is_file() else {}

    agent_flags = flags_at(directory / "artifacts/app/isolation.json")
    verifier_flags = flags_at(directory / "verifier/isolation.json")
    # 比较原始字节，但只记录命中数量；任何值都不进入报告或错误消息。
    output_credentials = credentials + [
        value.encode()
        for name, value in os.environ.items()
        if value and name in {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "E2B_API_KEY"}
    ]
    secret_hits = 0
    scanned = 0
    for file in directory.rglob("*"):
        if file.is_file() and not file.is_symlink():
            scanned += 1
            secret_hits += int(any(value in file.read_bytes() for value in output_credentials))
    paginator = AsyncSandbox.list()
    remaining = 0
    while paginator.has_next:
        remaining += len(await paginator.next_items())
    passed = (
        summary.outcome == "SCORED"
        and summary.rewards == {"reward": 1.0}
        and boundary["private_tests_absent_before_agent"]
        and agent_flags.get("tests_visible") is False
        and agent_flags.get("upstream_credential_visible") is False
        and agent_flags.get("E2B_API_KEY") is False
        and not any(
            verifier_flags.get(name, True)
            for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "E2B_API_KEY")
        )
        and verifier_flags.get("tests_visible") is True
        and not secret_hits
        and not remaining
        and content_manifest(task).digest == digest
    )
    evidence = {
        "separate_verifier": separate_verifier,
        "private_verifier_passed": (
            separate_verifier
            and boundary.get("agent_sandbox_deleted_before_verifier") is True
            and agent_flags.get("tests_visible") is False
            and verifier_flags.get("tests_visible") is True
            and summary.rewards == {"reward": 1.0}
            and remaining == 0
        ),
        "passed": passed,
        "runtime": runtime_versions(),
        "task_digest": digest,
        "summary": summary.model_dump(mode="json"),
        "boundary": boundary,
        "agent_flags": agent_flags,
        "verifier_flags": verifier_flags,
        "output_files_scanned": scanned,
        "files_with_credential_match": secret_hits,
        "sandboxes_remaining": remaining,
    }
    (root / "isolation.json").write_text(json.dumps(evidence, indent=2))
    print(json.dumps(evidence), flush=True)
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--separate-verifier", action="store_true")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.spike")
    args = parser.parse_args()
    if not args.run:
        print("需显式传入 --run；测试 Agent 工具是否能读取凭证环境及私密测试文件。")
        return 0
    load_dotenv(args.env_file, override=False)
    spec = spec_from_env(os.environ, ROOT / "examples/tasks/hello-task", ROOT / "outputs/spike")
    errors = preflight(spec, os.environ)
    if errors:
        print(json.dumps({"preflight_errors": errors}))
        return 2
    os.environ["ANTHROPIC_BASE_URL"] = spec.model_base_url
    os.umask(0o077)
    root = ROOT / "outputs/spike" / ("isolation-" + uuid4().hex)
    root.mkdir(parents=True, mode=0o700)
    try:
        return asyncio.run(run(root, args.separate_verifier))
    except Exception as exc:
        print(json.dumps({"error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
