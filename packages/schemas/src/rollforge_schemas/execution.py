"""S1 单 Trial 持久化契约；只允许显式的非敏感快照字段。"""

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rollforge_schemas.domain import RevisionRef, TrialStatus
from rollforge_schemas.runnable import RunnableBinding
from rollforge_schemas.runtime import ResultOutcome


class Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class RegistrySelection(Contract):
    """Job 所选不可变 Registry revision 的摘要引用，不含凭证。"""

    task: RevisionRef
    agent: RevisionRef
    model: RevisionRef


class ExecutionSnapshot(Contract):
    schema_version: Literal["1"] = "1"
    task: RevisionRef
    agent: RevisionRef
    model: RevisionRef
    timeout_sec: int = Field(default=300, ge=1, le=3600)
    max_executions: int = Field(default=3, ge=1, le=10)
    runtime: RunnableBinding | None = None
    registry: RegistrySelection | None = None

    @model_validator(mode="after")
    def pinned_binding(self):
        if self.runtime and (
            self.agent.digest != self.runtime.agent_digest
            or self.model.digest != self.runtime.model_digest
        ):
            raise ValueError("Agent/model revisions must match runnable binding")
        return self


class CreateJob(Contract):
    job_id: UUID
    owner_id: UUID
    snapshot: ExecutionSnapshot
    trial_count: int = Field(default=1, ge=1, le=1000, strict=True)


class ExecutionStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


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
    job_id: UUID
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
    attempt_index: int = Field(default=0, ge=0, lt=1000, strict=True)
    fencing_token: int = Field(ge=0)
    result: ResultCommit | None = None


class JobView(Contract):
    job_id: UUID
    owner_id: UUID
    snapshot: ExecutionSnapshot
    trial: TrialView
    trial_count: int = Field(ge=1, le=1000)
    terminal_trial_count: int = Field(ge=0, le=1000)


class JobSummary(Contract):
    job_id: UUID
    trial: TrialView
    trial_count: int = Field(ge=1, le=1000)
    terminal_trial_count: int = Field(ge=0, le=1000)


class JobList(Contract):
    items: tuple[JobSummary, ...]
    next_cursor: UUID | None = None


class ExecutionView(Contract):
    execution_id: UUID
    execution_sequence: int = Field(ge=1, strict=True)
    attempt_index: int = Field(default=0, ge=0, lt=1000, strict=True)
    fencing_token: int = Field(ge=1)
    status: ExecutionStatus
    expires_at: datetime
    result: ResultCommit | None = None


class ExecutionList(Contract):
    items: tuple[ExecutionView, ...]
    next_cursor: int | None = None


class TrialList(Contract):
    items: tuple[TrialView, ...]
    next_cursor: int | None = None


def validate_execution_transition(current: ExecutionStatus, target: ExecutionStatus) -> None:
    if current == target:
        return
    if current != ExecutionStatus.RUNNING or target == ExecutionStatus.RUNNING:
        raise ValueError("Invalid execution transition")
