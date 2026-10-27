"""专用 PostgreSQL 验收；每个测试创建独立 schema，仅清理自己创建的资源。"""

import asyncio
import hashlib
import json
import os
import secrets
import socket
import stat
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import uvicorn
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from pydantic import SecretStr
from rollforge_api.db import Base
from rollforge_api.execution_service import ExecutionService, LeaseRejected, SubmissionConflict
from rollforge_api.main import create_app
from rollforge_api.models import Execution, Trial
from rollforge_common.settings import Settings
from rollforge_hub_sdk.client import HubClient, HubError
from rollforge_schemas.api import FinishRequest, LeaseReference
from rollforge_schemas.domain import RevisionRef, TrialStatus
from rollforge_schemas.execution import CreateJob, ExecutionSnapshot, LeaseIdentity, ResultCommit
from rollforge_schemas.runnable import RunnableBinding
from sqlalchemy import func, select, text, update

ROOT = Path(__file__).resolve().parents[2]


@asynccontextmanager
async def serve_app(app):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    host, port = listener.getsockname()
    server = uvicorn.Server(
        uvicorn.Config(app, host=host, port=port, log_level="critical", access_log=False)
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(500):
            if server.started:
                break
            if task.done():
                await task
            await asyncio.sleep(0.01)
        if not server.started:
            raise TimeoutError("Hub HTTP test server did not start")
        yield f"http://{host}:{port}"
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=10)
        finally:
            listener.close()


def migration(connection, action):
    config = Config(str(ROOT / "apps/hub-api/alembic.ini"))
    config.attributes["connection"] = connection
    if action == "up":
        command.upgrade(config, "head")
    else:
        command.downgrade(config, "base")


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


async def test_fifo_scheduler_orders_trials_by_enqueue_time_and_sample(engine):
    service = ExecutionService(engine, global_execution_limit=3)
    first = request()
    first = first.model_copy(update={"trial_count": 2})
    second = request()
    await service.create_job(first)
    await service.create_job(second)
    async with engine.begin() as connection:
        await connection.execute(
            update(Trial)
            .where(Trial.job_id == first.job_id)
            .values(queued_at=datetime(2026, 1, 1, tzinfo=UTC))
        )
        await connection.execute(
            update(Trial)
            .where(Trial.job_id == second.job_id)
            .values(queued_at=datetime(2026, 1, 2, tzinfo=UTC))
        )
    leases = [await service.claim(uuid4()) for _ in range(3)]
    assert [lease.job_id for lease in leases] == [first.job_id, first.job_id, second.job_id]
    assert [lease.snapshot.task.id for lease in leases] == [
        first.snapshot.task.id,
        first.snapshot.task.id,
        second.snapshot.task.id,
    ]
    async with engine.connect() as connection:
        attempts = [
            await connection.scalar(select(Trial.attempt_index).where(Trial.id == lease.trial_id))
            for lease in leases
        ]
    assert attempts == [0, 1, 0]


async def test_global_capacity_is_shared_by_execution_services_and_recovery(engine):
    first_service = ExecutionService(engine, global_execution_limit=2)
    second_service = ExecutionService(engine, global_execution_limit=2)
    job = await first_service.create_job(request().model_copy(update={"trial_count": 4}))

    leases = await asyncio.gather(
        *((first_service if index % 2 else second_service).claim(uuid4()) for index in range(8))
    )
    active = [lease for lease in leases if lease is not None]
    assert len(active) == 2
    assert await second_service.claim(uuid4()) is None

    committed = await first_service.finish(active[0], result(job, active[0]))
    assert committed.status == TrialStatus.COMPLETED
    running = await second_service.claim(uuid4())
    assert running is not None

    stale = active[1]
    await expire(engine, stale)
    # An expired lease still owns a capacity slot until PostgreSQL recovery fences it.
    assert await first_service.claim(uuid4()) is None
    assert await second_service.recover_expired() == 1
    replacement = await first_service.claim(uuid4())
    assert replacement is not None
    assert replacement.trial_id != stale.trial_id
    assert await first_service.claim(uuid4()) is None
    assert (await first_service.finish(replacement, result(job, replacement))).status == (
        TrialStatus.COMPLETED
    )
    retried = await second_service.claim(uuid4())
    assert retried is not None
    assert retried.trial_id == stale.trial_id
    assert retried.execution_id != stale.execution_id
    assert retried.fencing_token == stale.fencing_token + 1


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


async def test_sigkill_worker_once_recovers_and_enforces_retry_budget(engine, tmp_path):
    """A killed real Worker process is fenced through the authenticated Hub API."""
    service = ExecutionService(engine)
    job = request(max_executions=2)
    binding = RunnableBinding(
        task_archive_key="tasks/task.tar.gz",
        task_archive_digest="sha256:" + "a" * 64,
        model_base_url="https://gateway.invalid",
    )
    snapshot = job.snapshot.model_copy(
        update={
            "agent": job.snapshot.agent.model_copy(update={"digest": binding.agent_digest}),
            "model": job.snapshot.model.model_copy(update={"digest": binding.model_digest}),
            "runtime": binding,
        }
    )
    job = job.model_copy(update={"snapshot": snapshot, "trial_count": 1})
    await service.create_job(job)

    worker_id = uuid4()
    token = secrets.token_urlsafe(32)
    settings = Settings(
        _env_file=None,
        control_plane_writes_enabled=True,
        api_credentials=SecretStr(
            json.dumps(
                [
                    {
                        "subject_id": str(worker_id),
                        "role": "WORKER",
                        "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                    }
                ]
            )
        ),
    )
    app = create_app(settings, engine=engine)

    async with serve_app(app) as hub_url:
        async with HubClient(hub_url, SecretStr(token)) as hub:

            async def claim_then_kill():
                process_env = {
                    "ROLLFORGE_TEST_HUB_URL": hub_url,
                    "ROLLFORGE_TEST_WORKER_TOKEN": token,
                    "ROLLFORGE_TEST_SPOOL": str(tmp_path / "worker-spool"),
                    "PYTHONPATH": os.pathsep.join(
                        filter(None, [str(ROOT), os.environ.get("PYTHONPATH", "")])
                    ),
                }
                if os.environ.get("PATH"):
                    process_env["PATH"] = os.environ["PATH"]
                process = subprocess.Popen(
                    [sys.executable, str(ROOT / "tests/integration/process_claim_fixture.py")],
                    cwd=ROOT,
                    env=process_env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
                try:
                    line = await asyncio.wait_for(
                        asyncio.to_thread(process.stdout.readline), timeout=10
                    )
                    assert line, "Worker exited before entering the test execution block"
                    lease = LeaseIdentity.model_validate_json(line)
                    process.kill()
                    await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=10)
                    return lease
                finally:
                    if process.poll() is None:
                        process.kill()
                        await asyncio.to_thread(process.wait)
                    if process.stdout:
                        process.stdout.close()

            async def reject_stale_finish(lease):
                body = FinishRequest(
                    lease=LeaseReference(
                        trial_id=lease.trial_id,
                        execution_id=lease.execution_id,
                        fencing_token=lease.fencing_token,
                    ),
                    result=result(job, lease),
                )
                with pytest.raises(HubError) as rejected:
                    await hub.finish(body)
                assert rejected.value.status_code == 409

            first = await claim_then_kill()
            assert first.fencing_token == 1
            spool_lease = tmp_path / "worker-spool" / str(first.execution_id) / "lease.json"
            assert spool_lease.is_file()
            assert stat.S_IMODE(spool_lease.stat().st_mode) == 0o600
            await expire(engine, first)
            assert (await hub.recover_expired()).recovered_count == 1

            second = await claim_then_kill()
            assert second.trial_id == first.trial_id
            assert second.execution_id != first.execution_id
            assert second.fencing_token == first.fencing_token + 1
            await reject_stale_finish(first)

            await expire(engine, second)
            assert (await hub.recover_expired()).recovered_count == 1
            await reject_stale_finish(second)
            assert await hub.claim() is None

    current = await service.get_job(job.job_id, job.owner_id)
    assert current.trial.status == TrialStatus.FAILED


async def test_active_renewal_and_failed_submission(engine):
    service = ExecutionService(engine)
    job = await service.create_job(request())
    # Keep enough headroom for remote PostgreSQL test hosts; lease expiry has a dedicated test.
    lease = await service.claim(uuid4(), lease_seconds=30)
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


async def test_multi_trial_migration_backfills_legacy_job_and_execution(engine):
    from uuid import uuid4

    config = Config(str(ROOT / "apps/hub-api/alembic.ini"))
    job_id, owner_id, trial_id, execution_id, worker_id = [uuid4() for _ in range(5)]
    legacy_snapshot = request().snapshot.model_dump_json()
    async with engine.begin() as connection:
        config.attributes["connection"] = connection.sync_connection
        await connection.run_sync(lambda sync: command.downgrade(config, "0002_registry"))
        await connection.execute(
            text("""
                INSERT INTO jobs (id, owner_id, snapshot)
                VALUES (:id, :owner, CAST(:snapshot AS jsonb))
            """),
            {"id": job_id, "owner": owner_id, "snapshot": legacy_snapshot},
        )
        await connection.execute(
            text("""
                INSERT INTO trials (id, job_id, status, attempt_index, fencing_token)
                VALUES (:id, :job, 'RUNNING', 0, 1)
            """),
            {"id": trial_id, "job": job_id},
        )
        await connection.execute(
            text("""
                INSERT INTO executions (
                    id, trial_id, worker_id, fencing_token, status, expires_at, result
                ) VALUES (:id, :trial, :worker, 1, 'RUNNING', now() + interval '5 minutes', NULL)
            """),
            {"id": execution_id, "trial": trial_id, "worker": worker_id},
        )
        await connection.run_sync(lambda sync: command.upgrade(config, "head"))
        assert (
            await connection.scalar(
                text("SELECT trial_count FROM jobs WHERE id = :id"), {"id": job_id}
            )
            == 1
        )
        assert (
            await connection.scalar(
                text("SELECT execution_sequence FROM jobs WHERE id = :id"), {"id": job_id}
            )
            == 1
        )
        migrated = (
            await connection.execute(
                text("SELECT job_id, job_sequence FROM executions WHERE id = :id"),
                {"id": execution_id},
            )
        ).one()
        assert migrated == (job_id, 1)
        assert (
            await connection.run_sync(
                lambda sync: compare_metadata(MigrationContext.configure(sync), Base.metadata)
            )
            == []
        )


async def test_multi_trial_migration_downgrade_refuses_to_drop_samples(engine):
    from sqlalchemy.exc import DBAPIError

    service = ExecutionService(engine)
    batch = request().model_copy(update={"trial_count": 2})
    await service.create_job(batch)
    config = Config(str(ROOT / "apps/hub-api/alembic.ini"))

    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: downgrade_to_registry(sync, config))

    saved = await service.get_job(batch.job_id, batch.owner_id)
    assert saved is not None and saved.trial_count == 2


def downgrade_to_registry(connection, config):
    config.attributes["connection"] = connection
    command.downgrade(config, "0002_registry")
