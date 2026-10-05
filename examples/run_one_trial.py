"""Run with the isolated runtime; default mode is non-executing preflight."""

import argparse
import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ValidationError
from rollforge_harbor_adapter.manifest import content_manifest
from rollforge_harbor_adapter.preflight import preflight, runtime_versions, spec_from_env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env.spike"))
    parser.add_argument("--task", type=Path, default=Path("examples/tasks/hello-task"))
    parser.add_argument("--output", type=Path, default=Path("outputs/spike"))
    parser.add_argument("--run", action="store_true", help="Create sandbox and call real inference")
    parser.add_argument("--force-build", action="store_true", help="通过 Harbor 重建模板")
    args = parser.parse_args()
    load_dotenv(args.env_file, override=False)
    try:
        spec = spec_from_env(os.environ, args.task, args.output)
        spec = spec.model_copy(update={"force_build": args.force_build})
    except ValidationError as exc:
        # Pydantic errors can contain input values. Only report field names.
        fields = [".".join(str(part) for part in error["loc"]) for error in exc.errors()]
        print(json.dumps({"status": "configuration_required", "fields": fields}))
        return 2
    errors = preflight(spec, os.environ)
    if errors:
        print(json.dumps({"status": "preflight_failed", "errors": errors}))
        return 2
    try:
        task_manifest = content_manifest(spec.task_dir)
        # Parse the real task with Harbor even for offline preflight.
        from harbor.models.task.task import Task
        from rollforge_harbor_adapter.native import build_trial_config

        Task(spec.task_dir)
        build_trial_config(spec, "preflight")
    except Exception as exc:
        print(json.dumps({"status": "invalid_task_or_config", "error_type": type(exc).__name__}))
        return 2
    if not args.run:
        print(
            json.dumps(
                {
                    "status": "offline_preflight_passed",
                    "runtime": runtime_versions(),
                    "task_digest": task_manifest.digest,
                    "real_execution": False,
                }
            )
        )
        return 0
    # Host-side Terminus consumes OPENAI_API_KEY. Installed Harness uses canonical env.
    endpoint_name = "ANTHROPIC_BASE_URL" if spec.agent == "claude-code" else "OPENAI_BASE_URL"
    os.environ[endpoint_name] = spec.model_base_url
    spec.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Existing output roots can predate this CLI and contain sensitive raw logs.
    spec.output_dir.chmod(0o700)
    try:
        from rollforge_harbor_adapter.native import run_native_trial
        from rollforge_harbor_adapter.results import summarize_result

        directory = asyncio.run(run_native_trial(spec))
        from harbor.models.trial.result import TrialResult

        TrialResult.model_validate_json((directory / "result.json").read_text())
        if content_manifest(spec.task_dir).digest != task_manifest.digest:
            raise RuntimeError("Task content changed during execution")
        summary = summarize_result(directory)
        # Manifest captures native output before the evidence file itself is written.
        # Raw output stays local/ignored. Evidence is not uploaded by this tool.
        evidence = {
            "runtime": runtime_versions(),
            "task_digest": task_manifest.digest,
            "summary": summary.model_dump(mode="json"),
            "output_manifest": content_manifest(directory).model_dump(mode="json"),
        }
        (directory / "rollforge-evidence.json").write_text(json.dumps(evidence, indent=2))
        print(
            json.dumps(
                {"status": summary.outcome, "output_dir": str(directory), "real_execution": True}
            )
        )
        return 0 if summary.outcome == "SCORED" else 1
    except (Exception, KeyboardInterrupt) as exc:
        print(json.dumps({"status": "runtime_failed", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
