from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TrialStatus(StrEnum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ExecutionPhase(StrEnum):
    LEASED = "LEASED"
    PROVISIONING = "PROVISIONING"
    ENV_SETUP = "ENV_SETUP"
    AGENT_SETUP = "AGENT_SETUP"
    AGENT_RUNNING = "AGENT_RUNNING"
    VERIFYING = "VERIFYING"
    COLLECTING = "COLLECTING"


class RevisionRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: UUID
    revision: int = Field(ge=1)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class TrialIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    job_id: UUID
    task: RevisionRef
    agent: RevisionRef
    model: RevisionRef
    attempt_index: int = Field(ge=0)


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
