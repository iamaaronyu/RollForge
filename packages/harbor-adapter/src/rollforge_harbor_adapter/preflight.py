import importlib.metadata
from collections.abc import Mapping
from pathlib import Path

from rollforge_schemas.runtime import RuntimeSpec

HARBOR_VERSION = "0.24.0"
E2B_VERSION = "2.25.0"


def runtime_versions() -> dict[str, str | None]:
    versions = {}
    for package in ("harbor", "e2b"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def preflight(spec: RuntimeSpec, env: Mapping[str, str]) -> list[str]:
    """Offline checks; passing does not certify network or sandbox readiness."""
    errors = []
    for package, expected in (("harbor", HARBOR_VERSION), ("e2b", E2B_VERSION)):
        if runtime_versions()[package] != expected:
            errors.append(f"Install pinned {package} {expected} in the isolated runtime")
    for name in ("E2B_API_KEY", "E2B_DOMAIN"):
        if not env.get(name):
            errors.append(f"Missing local {name}")
    if env.get("E2B_DOMAIN", "").lower() in {"e2b.dev", "e2b.app"}:
        errors.append("S0 requires an explicitly configured self-hosted E2B domain")
    key_name = "ANTHROPIC_API_KEY" if spec.agent == "claude-code" else "OPENAI_API_KEY"
    if not env.get(key_name):
        errors.append(f"Missing local {key_name}")
    for relative in ("task.toml", "instruction.md", "environment/Dockerfile", "tests/test.sh"):
        if not (spec.task_dir / relative).is_file():
            errors.append(f"Task is missing {relative}")
    if spec.output_dir.resolve() == spec.task_dir.resolve() or spec.task_dir.resolve() in (
        spec.output_dir.resolve().parents
    ):
        errors.append("Output directory must be outside the immutable task")
    return errors


def spec_from_env(env: Mapping[str, str], task: Path, output: Path) -> RuntimeSpec:
    return RuntimeSpec(
        agent=env.get("ROLLFORGE_SPIKE_AGENT", "terminus-2"),
        protocol=env.get("ROLLFORGE_SPIKE_PROTOCOL", "openai-chat-completions"),
        model_name=env.get("ROLLFORGE_SPIKE_MODEL", ""),
        model_base_url=env.get("ROLLFORGE_SPIKE_MODEL_BASE_URL", ""),
        agent_version=env.get("ROLLFORGE_SPIKE_AGENT_VERSION") or None,
        task_dir=task,
        output_dir=output,
        timeout_sec=env.get("ROLLFORGE_SPIKE_TIMEOUT_SEC", "300"),
    )
