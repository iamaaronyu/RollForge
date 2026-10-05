from pathlib import Path

import pytest
from pydantic import ValidationError
from rollforge_schemas.runtime import RuntimeSpec


def inputs(**changes):
    values = dict(
        agent="terminus-2",
        protocol="openai-chat-completions",
        model_name="openai/test",
        model_base_url="https://example.invalid/v1",
        task_dir=Path("task"),
        output_dir=Path("outputs"),
    )
    return values | changes


@pytest.mark.parametrize(
    "agent,protocol",
    [
        ("codex", "openai-chat-completions"),
        ("claude-code", "openai-responses"),
        ("terminus-2", "anthropic-messages"),
    ],
)
def test_protocol_mismatch_fails_before_spending(agent, protocol):
    with pytest.raises(ValidationError):
        RuntimeSpec(**inputs(agent=agent, protocol=protocol, agent_version="1.0.0"))


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://user:password@example.invalid/v1",
        "https://example.invalid/v1?key=secret",
        "https://example.invalid/v1#secret",
        "file:///etc/passwd",
        "not-a-url",
    ],
)
def test_secrets_cannot_be_embedded_in_endpoint(endpoint):
    with pytest.raises(ValidationError):
        RuntimeSpec(**inputs(model_base_url=endpoint))


@pytest.mark.parametrize("version", [None, "", "latest"])
def test_installed_agent_requires_version(version):
    with pytest.raises(ValidationError):
        RuntimeSpec(**inputs(agent="codex", protocol="openai-responses", agent_version=version))
