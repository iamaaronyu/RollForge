import importlib.metadata
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

from rollforge_schemas.runtime import RuntimeSpec

HARBOR_VERSION = "0.24.0"
E2B_VERSION = "2.46.0"


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
    if not env.get("E2B_API_KEY"):
        errors.append("Missing local E2B_API_KEY")
    endpoints = (env.get("E2B_API_URL"), env.get("E2B_SANDBOX_URL"))
    if any(endpoints):
        if not all(endpoints):
            errors.append("Configure both E2B_API_URL and E2B_SANDBOX_URL")
        for name, value in zip(("E2B_API_URL", "E2B_SANDBOX_URL"), endpoints, strict=True):
            if value:
                try:
                    url = urlsplit(value)
                    valid = (
                        url.scheme in {"http", "https"}
                        and bool(url.hostname)
                        and url.hostname not in {"e2b.dev", "e2b.app", "api.e2b.app", "api.e2b.dev"}
                        and not (url.username or url.password or url.query or url.fragment)
                        and url.path in {"", "/"}
                    )
                    _ = url.port
                except ValueError:
                    valid = False
                if not valid:
                    errors.append(f"Invalid self-hosted {name}")
    elif not env.get("E2B_DOMAIN"):
        errors.append("Missing local E2B_DOMAIN")
    if env.get("E2B_DOMAIN", "").lower() in {"e2b.dev", "e2b.app"}:
        errors.append("S0 requires an explicitly configured self-hosted E2B domain")
    if spec.agent == "claude-code":
        if not (env.get("ANTHROPIC_API_KEY") or env.get("ANTHROPIC_AUTH_TOKEN")):
            errors.append("Missing local ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN")
        for name in ("CLAUDE_FORCE_OAUTH", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX"):
            if env.get(name, "").lower() not in {"", "0", "false"}:
                errors.append(f"Remove conflicting {name} for the official DeepSeek API")
    elif not env.get("OPENAI_API_KEY"):
        errors.append("Missing local OPENAI_API_KEY")
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
        agent=env.get("ROLLFORGE_SPIKE_AGENT", "claude-code"),
        protocol=env.get("ROLLFORGE_SPIKE_PROTOCOL", "anthropic-messages"),
        model_name=env.get("ROLLFORGE_SPIKE_MODEL", "deepseek-flash"),
        model_base_url=env.get(
            "ROLLFORGE_SPIKE_MODEL_BASE_URL", "https://api.deepseek.com/anthropic"
        ),
        agent_version=env.get("ROLLFORGE_SPIKE_AGENT_VERSION", "2.1.81") or None,
        task_dir=task,
        output_dir=output,
        timeout_sec=env.get("ROLLFORGE_SPIKE_TIMEOUT_SEC", "300"),
    )
