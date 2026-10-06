"""控制面 HTTP 契约；身份由认证层提供，不能由请求指定。"""

from enum import StrEnum
from uuid import UUID

from pydantic import Field

from rollforge_schemas.execution import Contract, ExecutionSnapshot, ResultCommit


class Role(StrEnum):
    USER = "USER"
    WORKER = "WORKER"


class Principal(Contract):
    subject_id: UUID
    role: Role


class Credential(Principal):
    token_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class JobCreateRequest(Contract):
    job_id: UUID
    snapshot: ExecutionSnapshot


class ClaimRequest(Contract):
    lease_seconds: int = Field(default=60, ge=1, le=300, strict=True)
    runnable_only: bool = False


class LeaseReference(Contract):
    trial_id: UUID
    execution_id: UUID
    fencing_token: int = Field(ge=1, strict=True)


class RenewRequest(Contract):
    lease: LeaseReference
    lease_seconds: int = Field(default=60, ge=1, le=300, strict=True)


class FinishRequest(Contract):
    lease: LeaseReference
    result: ResultCommit


class ErrorCode(StrEnum):
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    WRITES_DISABLED = "WRITES_DISABLED"
    NOT_FOUND = "NOT_FOUND"
    LEASE_REJECTED = "LEASE_REJECTED"
    CONFLICT = "CONFLICT"
    INVALID_REQUEST = "INVALID_REQUEST"
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    STORAGE_UNAVAILABLE = "STORAGE_UNAVAILABLE"


class ApiError(Contract):
    code: ErrorCode
    message: str
