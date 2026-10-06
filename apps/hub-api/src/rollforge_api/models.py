from datetime import datetime
from uuid import UUID

from rollforge_schemas.domain import TrialStatus
from rollforge_schemas.execution import ExecutionStatus
from sqlalchemy import JSON, CheckConstraint, DateTime, Enum, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from rollforge_api.db import Base

document = JSON().with_variant(JSONB(), "postgresql")


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(index=True)
    snapshot: Mapped[dict] = mapped_column(document)


class Trial(Base):
    __tablename__ = "trials"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_trials_single_job"),
        CheckConstraint("attempt_index = 0", name="ck_trials_attempt"),
        CheckConstraint("fencing_token >= 0", name="ck_trials_token"),
        Index("ix_trials_queue", "status", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id"))
    status: Mapped[TrialStatus] = mapped_column(
        Enum(TrialStatus, native_enum=False, create_constraint=True, name="trial_status")
    )
    attempt_index: Mapped[int] = mapped_column(default=0)
    fencing_token: Mapped[int] = mapped_column(default=0)


class Execution(Base):
    __tablename__ = "executions"
    __table_args__ = (
        UniqueConstraint("trial_id", "fencing_token", name="uq_execution_token"),
        CheckConstraint("fencing_token > 0", name="ck_execution_token"),
        Index("ix_execution_expiry", "status", "expires_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    trial_id: Mapped[UUID] = mapped_column(ForeignKey("trials.id"))
    worker_id: Mapped[UUID]
    fencing_token: Mapped[int]
    status: Mapped[ExecutionStatus] = mapped_column(
        Enum(ExecutionStatus, native_enum=False, create_constraint=True, name="execution_status")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict | None] = mapped_column(document, nullable=True)
