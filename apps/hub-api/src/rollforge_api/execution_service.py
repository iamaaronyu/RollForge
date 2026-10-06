"""PostgreSQL 权威状态；每次执行权检查与写入均在同一事务内。"""

from collections.abc import Awaitable, Callable
from datetime import timedelta
from uuid import UUID, uuid4

from rollforge_schemas.domain import TrialStatus
from rollforge_schemas.execution import (
    CreateJob,
    ExecutionSnapshot,
    ExecutionStatus,
    JobView,
    Lease,
    LeaseIdentity,
    ResultCommit,
    TrialView,
    validate_execution_transition,
)
from rollforge_schemas.runtime import ResultOutcome
from rollforge_schemas.state_machine import validate_transition
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from rollforge_api.models import Execution, Job, Trial


class LeaseRejected(Exception):
    """不存在、过期或执行权已被替换。"""


class SubmissionConflict(Exception):
    """相同幂等标识对应不同内容。"""


class ExecutionService:
    def __init__(self, engine: AsyncEngine):
        if engine.dialect.name != "postgresql":
            raise ValueError("Execution service requires PostgreSQL")
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)

    @staticmethod
    def trial_state(trial: Trial, target: TrialStatus):
        validate_transition(trial.status, target)
        trial.status = target

    @staticmethod
    def execution_state(execution: Execution, target: ExecutionStatus):
        validate_execution_transition(execution.status, target)
        execution.status = target

    @staticmethod
    async def now(session: AsyncSession):
        # clock_timestamp 在取得锁后读取，避免事务开始时间掩盖等待期间的过期。
        return await session.scalar(select(func.clock_timestamp()))

    @staticmethod
    def duration(seconds: int):
        if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= 300:
            raise ValueError("Lease duration must be 1..300 seconds")
        return timedelta(seconds=seconds)

    async def create_job(self, request: CreateJob) -> JobView:
        payload = request.snapshot.model_dump(mode="json")
        async with self.sessions.begin() as session:
            # 唯一键竞争由 PostgreSQL 处理；重复请求等待首次创建事务提交。
            created = await session.scalar(
                insert(Job)
                .values(id=request.job_id, owner_id=request.owner_id, snapshot=payload)
                .on_conflict_do_nothing(index_elements=[Job.id])
                .returning(Job.id)
            )
            job = await session.get(Job, request.job_id)
            saved = ExecutionSnapshot.model_validate(job.snapshot).model_dump(mode="json")
            if job.owner_id != request.owner_id or saved != payload:
                raise SubmissionConflict("Job identity already has different inputs")
            if created:
                trial = Trial(
                    id=uuid4(),
                    job_id=job.id,
                    status=TrialStatus.PENDING,
                    attempt_index=0,
                    fencing_token=0,
                )
                self.trial_state(trial, TrialStatus.QUEUED)
                session.add(trial)
                await session.flush()
            await session.scalar(select(Trial).where(Trial.job_id == job.id).with_for_update())
            return await self.view(session, job)

    async def view(self, session: AsyncSession, job: Job) -> JobView:
        trial = await session.scalar(select(Trial).where(Trial.job_id == job.id))
        result = None
        if trial.fencing_token:
            execution = await session.scalar(
                select(Execution).where(
                    Execution.trial_id == trial.id,
                    Execution.fencing_token == trial.fencing_token,
                )
            )
            if execution and execution.result:
                result = ResultCommit.model_validate(execution.result)
        return JobView(
            job_id=job.id,
            owner_id=job.owner_id,
            snapshot=ExecutionSnapshot.model_validate(job.snapshot),
            trial=TrialView(
                trial_id=trial.id,
                job_id=job.id,
                status=trial.status,
                fencing_token=trial.fencing_token,
                result=result,
            ),
        )

    async def get_job(self, job_id: UUID, owner_id: UUID) -> JobView | None:
        async with self.sessions.begin() as session:
            job = await session.scalar(
                select(Job).where(Job.id == job_id, Job.owner_id == owner_id)
            )
            # 与完成/回收相同的锁次序，防止跨两次 SELECT 读取不一致状态。
            if job is None:
                return None
            await session.scalar(select(Trial).where(Trial.job_id == job_id).with_for_update())
            return await self.view(session, job)

    async def claim(
        self, worker_id: UUID, lease_seconds: int = 60, runnable_only: bool = False
    ) -> Lease | None:
        duration = self.duration(lease_seconds)
        async with self.sessions.begin() as session:
            query = select(Trial).where(Trial.status == TrialStatus.QUEUED)
            if runnable_only:
                query = query.join(Job).where(Job.snapshot["runtime"].as_string().is_not(None))
            trial = await session.scalar(
                query.order_by(Trial.id).with_for_update(of=Trial, skip_locked=True).limit(1)
            )
            if trial is None:
                return None
            job = await session.get(Job, trial.job_id)
            snapshot = ExecutionSnapshot.model_validate(job.snapshot)
            if trial.fencing_token >= snapshot.max_executions:
                raise LeaseRejected("Execution budget exhausted")
            trial.fencing_token += 1
            self.trial_state(trial, TrialStatus.RUNNING)
            execution = Execution(
                id=uuid4(),
                trial_id=trial.id,
                worker_id=worker_id,
                fencing_token=trial.fencing_token,
                status=ExecutionStatus.RUNNING,
                expires_at=await self.now(session) + duration,
            )
            session.add(execution)
            return Lease(
                job_id=job.id,
                trial_id=trial.id,
                execution_id=execution.id,
                worker_id=worker_id,
                fencing_token=execution.fencing_token,
                expires_at=execution.expires_at,
                snapshot=snapshot,
            )

    async def owned(self, session: AsyncSession, lease: LeaseIdentity):
        trial = await session.get(Trial, lease.trial_id, with_for_update=True)
        if trial is None or trial.fencing_token != lease.fencing_token:
            raise LeaseRejected("Execution is not current")
        execution = await session.get(Execution, lease.execution_id, with_for_update=True)
        if (
            execution is None
            or execution.trial_id != trial.id
            or execution.worker_id != lease.worker_id
            or execution.fencing_token != lease.fencing_token
        ):
            raise LeaseRejected("Lease ownership mismatch")
        return trial, execution

    async def renew(self, lease: LeaseIdentity, lease_seconds: int = 60) -> Lease:
        duration = self.duration(lease_seconds)
        async with self.sessions.begin() as session:
            trial, execution = await self.owned(session, lease)
            now = await self.now(session)
            if (
                trial.status != TrialStatus.RUNNING
                or execution.status != ExecutionStatus.RUNNING
                or execution.expires_at <= now
            ):
                raise LeaseRejected("Lease is not active")
            execution.expires_at = now + duration
            job = await session.get(Job, trial.job_id)
            return Lease(
                job_id=job.id,
                **lease.model_dump(
                    include={"trial_id", "execution_id", "worker_id", "fencing_token"}
                ),
                expires_at=execution.expires_at,
                snapshot=ExecutionSnapshot.model_validate(job.snapshot),
            )

    async def finish(
        self,
        lease: LeaseIdentity,
        result: ResultCommit,
        verifier: Callable[[Lease, ResultCommit], Awaitable[None]] | None = None,
    ) -> TrialView:
        payload = result.model_dump(mode="json")
        async with self.sessions.begin() as session:
            trial, execution = await self.owned(session, lease)
            expected_key = (
                f"jobs/{trial.job_id}/trials/{trial.id}/executions/{execution.id}/manifest.json"
            )
            if result.manifest_key != expected_key:
                raise SubmissionConflict("Manifest must belong to this execution")
            if execution.result is not None:
                if execution.result != payload:
                    raise SubmissionConflict("Execution already has a different result")
                # 同一次已接受提交在租约结束后仍可重放；必须是当前执行者。
            else:
                if (
                    trial.status != TrialStatus.RUNNING
                    or execution.status != ExecutionStatus.RUNNING
                    or execution.expires_at <= await self.now(session)
                ):
                    raise LeaseRejected("Lease is not active")
                job = await session.get(Job, trial.job_id)
                snapshot = ExecutionSnapshot.model_validate(job.snapshot)
                if snapshot.runtime is not None:
                    if verifier is None:
                        raise SubmissionConflict("Runnable results require object verification")
                    if execution.expires_at <= await self.now(session):
                        raise LeaseRejected("Lease is not active")
                    await verifier(
                        Lease(
                            job_id=job.id,
                            trial_id=trial.id,
                            execution_id=execution.id,
                            worker_id=execution.worker_id,
                            fencing_token=execution.fencing_token,
                            expires_at=execution.expires_at,
                            snapshot=snapshot,
                        ),
                        result,
                    )
                if (
                    trial.status != TrialStatus.RUNNING
                    or execution.status != ExecutionStatus.RUNNING
                    or execution.expires_at <= await self.now(session)
                ):
                    raise LeaseRejected("Lease is not active")
                scored = result.outcome == ResultOutcome.SCORED
                self.trial_state(trial, TrialStatus.COMPLETED if scored else TrialStatus.FAILED)
                self.execution_state(
                    execution,
                    ExecutionStatus.COMPLETED if scored else ExecutionStatus.FAILED,
                )
                execution.result = payload
            return TrialView(
                trial_id=trial.id,
                job_id=trial.job_id,
                status=trial.status,
                fencing_token=trial.fencing_token,
                result=result,
            )

    async def recover_expired(self, limit: int = 100) -> int:
        if not 1 <= limit <= 1000:
            raise ValueError("Recovery batch must be 1..1000")
        async with self.sessions.begin() as session:
            trials = (
                await session.scalars(
                    select(Trial)
                    .join(
                        Execution,
                        (Execution.trial_id == Trial.id)
                        & (Execution.fencing_token == Trial.fencing_token),
                    )
                    .where(
                        Trial.status == TrialStatus.RUNNING,
                        Execution.status == ExecutionStatus.RUNNING,
                        Execution.expires_at <= func.clock_timestamp(),
                    )
                    .order_by(Trial.id)
                    .with_for_update(of=Trial, skip_locked=True)
                    .limit(limit)
                )
            ).all()
            recovered = 0
            for trial in trials:
                execution = await session.scalar(
                    select(Execution)
                    .where(
                        Execution.trial_id == trial.id,
                        Execution.fencing_token == trial.fencing_token,
                    )
                    .with_for_update()
                )
                # Join 的候选快照可能早于一次续租提交；锁定后必须再次校验。
                if (
                    execution.status != ExecutionStatus.RUNNING
                    or execution.expires_at > await self.now(session)
                ):
                    continue
                self.execution_state(execution, ExecutionStatus.EXPIRED)
                job = await session.get(Job, trial.job_id)
                snapshot = ExecutionSnapshot.model_validate(job.snapshot)
                target = (
                    TrialStatus.QUEUED
                    if trial.fencing_token < snapshot.max_executions
                    else TrialStatus.FAILED
                )
                self.trial_state(trial, target)
                recovered += 1
            return recovered
