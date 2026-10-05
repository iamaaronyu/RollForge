"""Real installed dependencies, offline contracts; these tests are not rollout E2E."""

import os
from pathlib import Path

import pytest
from e2b.connection_config import ConnectionConfig
from harbor.models.task.task import Task
from harbor.trial.trial import Trial
from rollforge_harbor_adapter.native import build_trial_config
from rollforge_harbor_adapter.preflight import runtime_versions
from rollforge_schemas.runtime import RuntimeSpec

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("task_name", ["hello-task", "coding-task"])
def test_real_task_and_trial_config(task_name):
    directory = ROOT / "examples/tasks" / task_name
    task = Task(directory)
    spec = RuntimeSpec(
        agent="terminus-2",
        protocol="openai-chat-completions",
        model_name="openai/test",
        model_base_url="https://example.invalid/v1",
        task_dir=directory,
        output_dir=ROOT / "outputs/spike",
    )
    config = build_trial_config(spec, "contract-test")
    assert task.config.schema_version == "1.4"
    assert config.environment.type.value == "e2b"
    assert config.environment.delete is True
    assert config.agent.kwargs == {"api_base": spec.model_base_url, "max_turns": 10}
    assert callable(Trial.create)


@pytest.mark.parametrize(
    "agent,protocol,version",
    [
        ("codex", "openai-responses", "0.118.0"),
        ("claude-code", "anthropic-messages", "2.0.0"),
    ],
)
def test_installed_agent_config_pins_cli_version(agent, protocol, version):
    # Versions here test the config field only, not their runtime compatibility.
    spec = RuntimeSpec(
        agent=agent,
        protocol=protocol,
        model_name="test-model",
        agent_version=version,
        model_base_url="https://example.invalid/v1",
        task_dir=ROOT / "examples/tasks/hello-task",
        output_dir=ROOT / "outputs/spike",
    )
    assert build_trial_config(spec, "contract-test").agent.kwargs["version"] == version


def test_pinned_runtime_versions():
    assert runtime_versions() == {"harbor": "0.24.0", "e2b": "2.46.0"}


def test_e2b_sdk_uses_self_host_environment(monkeypatch):
    monkeypatch.setenv("E2B_DOMAIN", "sandbox.example.invalid")
    monkeypatch.setenv("E2B_API_KEY", "unit-test-only")
    monkeypatch.delenv("E2B_API_URL", raising=False)
    monkeypatch.delenv("E2B_DEBUG", raising=False)
    config = ConnectionConfig()
    assert config.api_url == "https://api.sandbox.example.invalid"
    assert config.api_key == os.environ["E2B_API_KEY"]


def test_embed_sdk_endpoints_and_claude_options(monkeypatch):
    from harbor.agents.installed.claude_code import ClaudeCodeOptions
    from rollforge_harbor_adapter.preflight import spec_from_env

    monkeypatch.setenv("E2B_API_URL", "http://127.0.0.1:3300")
    monkeypatch.setenv("E2B_SANDBOX_URL", "http://127.0.0.1:3302")
    config = ConnectionConfig()
    assert config.api_url == "http://127.0.0.1:3300"
    spec = spec_from_env({}, ROOT / "examples/tasks/hello-task", ROOT / "outputs/spike")
    kwargs = build_trial_config(spec, "claude-contract").agent.kwargs
    options = ClaudeCodeOptions.model_validate(kwargs)
    assert options.max_turns == 10
    assert options.disable_web_search is True
    assert kwargs["version"] == "2.1.81"
