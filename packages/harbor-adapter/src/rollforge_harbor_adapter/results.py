import json
import math
from datetime import datetime
from pathlib import Path

from rollforge_schemas.runtime import NativeResultSummary, ResultOutcome


def _duration(timing: dict | None) -> float | None:
    if not timing or not timing.get("started_at") or not timing.get("finished_at"):
        return None
    start = datetime.fromisoformat(timing["started_at"].replace("Z", "+00:00"))
    finish = datetime.fromisoformat(timing["finished_at"].replace("Z", "+00:00"))
    value = (finish - start).total_seconds()
    if value < 0:
        raise ValueError("Native result contains negative phase duration")
    return value


def summarize_result(directory: Path) -> NativeResultSummary:
    """Project pinned single-step native output without inventing missing values."""
    path = directory / "result.json"
    if path.is_symlink() or not path.is_file():
        raise ValueError("Native result.json is missing or unsafe")
    payload = json.loads(path.read_text())
    if payload.get("step_results"):
        raise ValueError("S0 parser supports single-step trials only")
    if not payload.get("finished_at"):
        raise ValueError("Native trial is not finalized")
    verifier = payload.get("verifier_result") or {}
    rewards = verifier.get("rewards")
    if rewards is not None:
        if not isinstance(rewards, dict) or not rewards:
            raise ValueError("Reward map must be nonempty")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in rewards.values()
        ):
            raise ValueError("Reward values must be finite numbers")
    error = payload.get("exception_info")
    outcome = (
        ResultOutcome.RUNTIME_FAILED
        if error
        else ResultOutcome.SCORED
        if rewards is not None
        else ResultOutcome.UNVERIFIED
    )
    usage = payload.get("agent_result") or {}
    timing = {
        phase: _duration(payload.get(phase))
        for phase in (
            "environment_setup",
            "agent_setup",
            "agent_execution",
            "verifier",
        )
    }
    timing["total"] = _duration(payload)
    return NativeResultSummary(
        outcome=outcome,
        rewards=rewards,
        exception_type=error.get("exception_type") if error else None,
        input_tokens=usage.get("n_input_tokens"),
        output_tokens=usage.get("n_output_tokens"),
        cached_tokens=usage.get("n_cache_tokens"),
        timing=timing,
        trajectory_state="PRESENT"
        if (directory / "agent/trajectory.json").is_file()
        else "MISSING",
        artifact_manifest_state="PRESENT"
        if (directory / "artifacts/manifest.json").is_file()
        else "MISSING",
    )
