"""可运行绑定；不包含凭证或任意环境变量。"""

import hashlib
import json
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


def digest_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def canonical_bytes(data: dict) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def safe_key(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or value == "."
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or str(path) != value
    ):
        raise ValueError("Object path must be normalized and relative")
    return value


class RunnableBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    task_archive_key: str = Field(max_length=512)
    task_archive_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    agent_name: Literal["claude-code"] = "claude-code"
    agent_version: Literal["2.1.81"] = "2.1.81"
    protocol: Literal["anthropic-messages"] = "anthropic-messages"
    model_name: Literal["deepseek-flash"] = "deepseek-flash"
    model_base_url: str
    credential_mode: Literal["gateway-session"] = "gateway-session"
    verifier_mode: Literal["separate"] = "separate"

    _key = field_validator("task_archive_key")(safe_key)

    @field_validator("model_base_url")
    @classmethod
    def endpoint(cls, value: str):
        try:
            url = urlsplit(value)
            _port = url.port
        except ValueError:
            raise ValueError("Invalid gateway endpoint") from None
        if (
            url.scheme not in {"https", "http"}
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
        ):
            raise ValueError("Gateway endpoint cannot contain credentials or a path")
        return value.rstrip("/")

    @property
    def agent_digest(self):
        return digest_bytes(
            canonical_bytes({"agent_name": self.agent_name, "agent_version": self.agent_version})
        )

    @property
    def model_digest(self):
        return digest_bytes(
            canonical_bytes(
                {
                    "model_name": self.model_name,
                    "protocol": self.protocol,
                    "model_base_url": self.model_base_url,
                    "credential_mode": self.credential_mode,
                }
            )
        )
