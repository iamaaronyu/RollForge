"""S1 单 Trial 持久化契约；只允许显式的非敏感快照字段。"""

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rollforge_schemas.domain import RevisionRef, TrialStatus
from rollforge_schemas.runtime import ResultOutcome


class Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class ExecutionSnapshot(Contract):
    schema_version: Literal["1"] = "1"
    task: RevisionRef
    agent: RevisionRef
    model: RevisionRef
    timeout_sec: int = Field(default=300, ge=1, le=3600)
    max_executions: int = Field(default=3, ge=1, le=10)


class CreateJob(Contract):
    job_id: UUID
    owner_id: UUID
    snapshot: ExecutionSnapshot


class ExecutionStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class FailureReason(StrEnum):
    AGENT_ERROR = "AGENT_ERROR"
    VERIFIER_ERROR = "VERIFIER_ERROR"
    INFRA_ERROR = "INFRA_ERROR"
    UNVERIFIED = "UNVERIFIED"


class LeaseIdentity(Contract):
    trial_id: UUID
    execution_id: UUID
    worker_id: UUID
    fencing_token: int = Field(ge=1)


class Lease(LeaseIdentity):
    expires_at: datetime
    snapshot: ExecutionSnapshot


class ResultCommit(Contract):
    outcome: ResultOutcome
    rewards: dict[str, float] | None = None
    failure_reason: FailureReason | None = None
    manifest_key: str = Field(min_length=1, max_length=512)
    manifest_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_result(self):
        if self.outcome == ResultOutcome.SCORED:
            if not self.rewards or self.failure_reason is not None:
                raise ValueError("Scored results require rewards and no failure reason")
        elif self.rewards is not None or self.failure_reason is None:
            raise ValueError("Unscored results require a failure reason and no rewards")
        return self


class TrialView(Contract):
    trial_id: UUID
    job_id: UUID
    status: TrialStatus
    attempt_index: Literal[0] = 0
    fencing_token: int = Field(ge=0)
    result: ResultCommit | None = None


class JobView(Contract):
    job_id: UUID
    owner_id: UUID
    snapshot: ExecutionSnapshot
    trial: TrialView


def validate_execution_transition(current: ExecutionStatus, target: ExecutionStatus) -> None:
    if current == target:
        return
    if current != ExecutionStatus.RUNNING or target == ExecutionStatus.RUNNING:
        raise ValueError("Invalid execution transition")
