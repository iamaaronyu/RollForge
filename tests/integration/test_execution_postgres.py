"""专用 PostgreSQL 验收；每个测试创建独立 schema，仅清理自己创建的资源。"""

import asyncio
import os
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from rollforge_api.db import Base
from rollforge_api.execution_service import ExecutionService, LeaseRejected, SubmissionConflict
from rollforge_api.models import Execution, Trial
from rollforge_schemas.domain import RevisionRef, TrialStatus
from rollforge_schemas.execution import CreateJob, ExecutionSnapshot, ResultCommit
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[2]


def migration(connection, action):
    config = Config(str(ROOT / "apps/hub-api/alembic.ini"))
    config.attributes["connection"] = connection
    if action == "up":
        command.upgrade(config, "head")
    else:
        command.downgrade(config, "base")


@pytest_asyncio.fixture
async def engine():
    url = os.environ.get("ROLLFORGE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("真实 PostgreSQL 未配置：设置 ROLLFORGE_TEST_DATABASE_URL")
    if not url.startswith("postgresql+asyncpg://"):
        pytest.fail("验收必须使用 PostgreSQL + asyncpg")
    schema = "test_execution_" + uuid4().hex
    admin = create_async_engine(url)
    isolated = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    created = False
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            created = True
        async with isolated.begin() as connection:
            await connection.run_sync(lambda sync: migration(sync, "up"))
        yield isolated
    finally:
        await isolated.dispose()
        if created:
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


def request(max_executions=3):
    refs = [RevisionRef(id=uuid4(), revision=1, digest="sha256:" + "a" * 64) for _ in range(3)]
    return CreateJob(
        job_id=uuid4(),
        owner_id=uuid4(),
        snapshot=ExecutionSnapshot(
            task=refs[0],
            agent=refs[1],
            model=refs[2],
            max_executions=max_executions,
        ),
    )


def result(job, lease, reward=0):
    return ResultCommit(
        outcome="SCORED",
        rewards={"reward": reward},
        manifest_key=f"jobs/{job.job_id}/trials/{lease.trial_id}/executions/{lease.execution_id}/manifest.json",
        manifest_digest="sha256:" + "a" * 64,
    )


async def expire(engine, lease):
    async with engine.begin() as connection:
        await connection.execute(
            update(Execution)
            .where(Execution.id == lease.execution_id)
            .values(
                expires_at=func.clock_timestamp() - timedelta(seconds=1),
            )
        )


async def test_migration_round_trip_and_metadata(engine):
    async with engine.begin() as connection:
        differences = await connection.run_sync(
            lambda sync: compare_metadata(
                MigrationContext.configure(sync),
                Base.metadata,
            )
        )
        assert differences == []
        await connection.run_sync(lambda sync: migration(sync, "down"))
        assert await connection.scalar(text("SELECT to_regclass('jobs')")) is None
        await connection.run_sync(lambda sync: migration(sync, "up"))


async def test_competing_workers_and_create_idempotency(engine):
    service = ExecutionService(engine)
    inputs = request()
    first, duplicate = await asyncio.gather(service.create_job(inputs), service.create_job(inputs))
    assert first == duplicate
    leases = await asyncio.gather(service.claim(uuid4()), service.claim(uuid4()))
    assert sum(lease is not None for lease in leases) == 1
    changed = inputs.model_copy(update={"owner_id": uuid4()})
    with pytest.raises(SubmissionConflict):
        await service.create_job(changed)
    changed = inputs.model_copy(
        update={
            "snapshot": inputs.snapshot.model_copy(update={"timeout_sec": 600}),
        }
    )
    with pytest.raises(SubmissionConflict):
        await service.create_job(changed)
    assert await service.get_job(inputs.job_id, uuid4()) is None


async def test_skip_locked_claim_does_not_wait(engine):
    service = ExecutionService(engine)
    job = await service.create_job(request())
    async with service.sessions.begin() as holder:
        await holder.get(Trial, job.trial.trial_id, with_for_update=True)
        assert await asyncio.wait_for(service.claim(uuid4()), timeout=2) is None
    assert await service.claim(uuid4()) is not None


async def test_recovery_fences_all_old_writes_and_preserves_trial(engine):
    service = ExecutionService(engine)
    job = await service.create_job(request())
    old = await service.claim(uuid4())
    await expire(engine, old)
    with pytest.raises(LeaseRejected):
        await service.renew(old)
    with pytest.raises(LeaseRejected):
        await service.finish(old, result(job, old))
    counts = await asyncio.gather(service.recover_expired(), service.recover_expired())
    assert sum(counts) == 1
    new = await service.claim(uuid4())
    assert new.trial_id == old.trial_id and new.execution_id != old.execution_id
    assert new.fencing_token == old.fencing_token + 1
    for operation in (
        service.renew(old),
        service.finish(old, result(job, old)),
        service.finish(
            old,
            ResultCommit(
                outcome="RUNTIME_FAILED",
                failure_reason="INFRA_ERROR",
                manifest_key=result(job, old).manifest_key,
                manifest_digest="sha256:" + "a" * 64,
            ),
        ),
    ):
        with pytest.raises(LeaseRejected):
            await operation
    async with service.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Trial)) == 1
        assert await session.scalar(select(func.count()).select_from(Execution)) == 2
    view = await service.get_job(job.job_id, job.owner_id)
    assert view.trial.attempt_index == 0 and view.trial.status == TrialStatus.RUNNING


async def test_zero_score_idempotency_conflict_and_wrong_owner(engine):
    service = ExecutionService(engine)
    job = await service.create_job(request())
    lease = await service.claim(uuid4())
    with pytest.raises(LeaseRejected):
        await service.finish(lease.model_copy(update={"worker_id": uuid4()}), result(job, lease))
    with pytest.raises(SubmissionConflict):
        await service.finish(lease, result(job, lease).model_copy(update={"manifest_key": "other"}))
    committed = await service.finish(lease, result(job, lease))
    assert committed.status == TrialStatus.COMPLETED
    await expire(engine, lease)
    assert await service.finish(lease, result(job, lease)) == committed
    with pytest.raises(SubmissionConflict):
        await service.finish(lease, result(job, lease, reward=1))
    with pytest.raises(LeaseRejected):
        await service.renew(lease)
    assert await service.recover_expired() == 0


async def test_recovery_budget_and_service_restart(engine):
    service = ExecutionService(engine)
    job = await service.create_job(request(max_executions=1))
    lease = await service.claim(uuid4())
    await engine.dispose()
    restarted = ExecutionService(engine)
    assert (await restarted.get_job(job.job_id, job.owner_id)).trial.status == TrialStatus.RUNNING
    await expire(engine, lease)
    assert await restarted.recover_expired() == 1
    assert await restarted.claim(uuid4()) is None
    assert (await restarted.get_job(job.job_id, job.owner_id)).trial.status == TrialStatus.FAILED


async def test_active_renewal_and_failed_submission(engine):
    service = ExecutionService(engine)
    job = await service.create_job(request())
    lease = await service.claim(uuid4(), lease_seconds=1)
    renewed = await service.renew(lease, lease_seconds=60)
    assert renewed.expires_at > lease.expires_at
    assert await service.recover_expired() == 0
    failed = ResultCommit(
        outcome="RUNTIME_FAILED",
        failure_reason="INFRA_ERROR",
        manifest_key=result(job, lease).manifest_key,
        manifest_digest="sha256:" + "a" * 64,
    )
    assert (await service.finish(lease, failed)).status == TrialStatus.FAILED


async def test_concurrent_submissions_are_atomic(engine):
    service = ExecutionService(engine)
    job = await service.create_job(request())
    lease = await service.claim(uuid4())
    outcomes = await asyncio.gather(
        service.finish(lease, result(job, lease, reward=0)),
        service.finish(lease, result(job, lease, reward=1)),
        return_exceptions=True,
    )
    accepted = [item for item in outcomes if not isinstance(item, Exception)]
    rejected = [item for item in outcomes if isinstance(item, SubmissionConflict)]
    assert len(accepted) == len(rejected) == 1
    stored = await service.get_job(job.job_id, job.owner_id)
    assert stored.trial == accepted[0]
    replay = await asyncio.gather(
        service.finish(lease, accepted[0].result),
        service.finish(lease, accepted[0].result),
    )
    assert replay == [accepted[0], accepted[0]]
