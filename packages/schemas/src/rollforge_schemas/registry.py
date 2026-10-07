"""不可变资产版本契约；只接受显式的非凭证配置。"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator

from rollforge_schemas.execution import Contract
from rollforge_schemas.runnable import RunnableBinding, canonical_bytes, digest_bytes, safe_key


class TaskSpec(Contract):
    kind: Literal["TASK"] = "TASK"
    archive_key: str = Field(max_length=512)
    archive_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    _key = field_validator("archive_key")(safe_key)


class AgentSpec(Contract):
    kind: Literal["AGENT"] = "AGENT"
    agent_name: Literal["claude-code"] = "claude-code"
    agent_version: Literal["2.1.81"] = "2.1.81"


class ModelSpec(Contract):
    kind: Literal["MODEL"] = "MODEL"
    model_name: Literal["deepseek-flash"] = "deepseek-flash"
    protocol: Literal["anthropic-messages"] = "anthropic-messages"
    model_base_url: str
    credential_mode: Literal["gateway-session"] = "gateway-session"
    _endpoint = field_validator("model_base_url")(RunnableBinding.endpoint.__func__)


RevisionSpec = Annotated[TaskSpec | AgentSpec | ModelSpec, Field(discriminator="kind")]
AssetKind = Literal["TASK", "AGENT", "MODEL"]


class RevisionCreate(Contract):
    asset_id: UUID
    revision: int = Field(ge=1, le=1000000, strict=True)
    spec: RevisionSpec


class AssetRevision(RevisionCreate):
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class RevisionList(Contract):
    items: tuple[AssetRevision, ...]
    next_cursor: int | None = None


def revision_digest(spec: TaskSpec | AgentSpec | ModelSpec) -> str:
    """注册配置摘要与任务 archive 摘要、运行绑定摘要分别保存。"""
    return digest_bytes(canonical_bytes(spec.model_dump(mode="json")))
