from enum import StrEnum
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ModelProtocol(StrEnum):
    CHAT_COMPLETIONS = "openai-chat-completions"
    RESPONSES = "openai-responses"
    ANTHROPIC_MESSAGES = "anthropic-messages"


class RuntimeSpec(BaseModel):
    """Non-secret S0 inputs. Never put API keys in this object."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    agent: Literal["terminus-2", "codex", "claude-code"]
    protocol: ModelProtocol
    model_name: str = Field(min_length=1)
    model_base_url: str
    agent_version: str | None = None
    force_build: bool = False
    task_dir: Path
    output_dir: Path
    timeout_sec: int = Field(default=300, ge=1, le=3600)

    @field_validator("model_base_url")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        url = urlsplit(value)
        if url.scheme not in {"http", "https"} or not url.hostname:
            raise ValueError("Model endpoint must be an HTTP(S) URL")
        if url.username or url.password or url.query or url.fragment:
            raise ValueError("Model endpoint cannot contain credentials, query or fragment")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_compatibility(self):
        expected = {
            "terminus-2": ModelProtocol.CHAT_COMPLETIONS,
            "codex": ModelProtocol.RESPONSES,
            "claude-code": ModelProtocol.ANTHROPIC_MESSAGES,
        }
        if self.protocol != expected[self.agent]:
            raise ValueError("Chosen Harness and endpoint protocol do not match")
        if self.agent != "terminus-2" and (
            not self.agent_version or self.agent_version.lower() == "latest"
        ):
            raise ValueError("Installed Harness requires a pinned agent_version")
        if self.agent == "terminus-2" and not self.model_name.startswith("openai/"):
            raise ValueError("S0 Terminus OpenAI endpoint requires an openai/ model prefix")
        return self


class FileRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    path: str
    size: int = Field(ge=0)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    executable: bool = False


class ContentManifest(BaseModel):
    schema_version: Literal["1"] = "1"
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    files: list[FileRecord]


class ResultOutcome(StrEnum):
    SCORED = "SCORED"
    RUNTIME_FAILED = "RUNTIME_FAILED"
    UNVERIFIED = "UNVERIFIED"


class NativeResultSummary(BaseModel):
    schema_version: Literal["1"] = "1"
    parser_version: Literal["harbor-0.24.0-v1"] = "harbor-0.24.0-v1"
    outcome: ResultOutcome
    rewards: dict[str, float] | None
    exception_type: str | None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_tokens: int | None = Field(default=None, ge=0)
    timing: dict[str, float | None]
    trajectory_state: Literal["PRESENT", "MISSING"]
    artifact_manifest_state: Literal["PRESENT", "MISSING"]
