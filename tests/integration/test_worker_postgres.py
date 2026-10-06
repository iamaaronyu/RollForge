"""真实 PostgreSQL 的 runnable 领取与验收事务；不模拟 Harbor 运行验收。"""

import asyncio
from uuid import uuid4

import pytest
import test_object_store_minio as storage_tests
from rollforge_api.execution_service import ExecutionService, LeaseRejected, SubmissionConflict
from rollforge_object_store.s3 import ObjectStoreError
from rollforge_schemas.domain import RevisionRef, TrialStatus
from rollforge_schemas.execution import CreateJob, ExecutionSnapshot, ResultCommit
from rollforge_schemas.runnable import RunnableBinding

server = storage_tests.server
store = storage_tests.store
output = storage_tests.output


def request(runnable=True):
    binding = RunnableBinding(
        task_archive_key="tasks/task.tar.gz",
        task_archive_digest="sha256:" + "a" * 64,
        model_base_url="https://gateway.test",
    )
    return CreateJob(
        job_id=uuid4(),
        owner_id=uuid4(),
        snapshot=ExecutionSnapshot(
            task=RevisionRef(id=uuid4(), revision=1, digest="sha256:" + "a" * 64),
            agent=RevisionRef(id=uuid4(), revision=1, digest=binding.agent_digest),
            model=RevisionRef(id=uuid4(), revision=1, digest=binding.model_digest),
            runtime=binding if runnable else None,
        ),
    )


def result(lease):
    return ResultCommit(
        outcome="SCORED",
        rewards={"reward": 0.0},
        manifest_key=(
            f"jobs/{lease.job_id}/trials/{lease.trial_id}/"
            f"executions/{lease.execution_id}/manifest.json"
        ),
        manifest_digest="sha256:" + "a" * 64,
    )


async def test_runnable_only_claim_skips_metadata_jobs(engine):
    service = ExecutionService(engine)
    metadata = await service.create_job(request(False))
    runnable = await service.create_job(request())
    lease = await service.claim(uuid4(), runnable_only=True)
    assert lease.job_id == runnable.job_id and lease.snapshot.runtime is not None
    assert await service.claim(uuid4(), runnable_only=True) is None
    assert (await service.claim(uuid4())).job_id == metadata.job_id


async def test_verification_failure_rolls_back_and_accepted_replay_skips_storage(engine):
    service = ExecutionService(engine)
    job = await service.create_job(request())
    lease = await service.claim(uuid4(), runnable_only=True)
    with pytest.raises(SubmissionConflict):
        await service.finish(lease, result(lease))
    calls = []

    async def unavailable(current, submitted):
        calls.append((current, submitted))
        raise ObjectStoreError("storage unavailable")

    with pytest.raises(ObjectStoreError):
        await service.finish(lease, result(lease), unavailable)
    assert (await service.get_job(job.job_id, job.owner_id)).trial.status == TrialStatus.RUNNING

    async def verified(current, submitted):
        assert current.job_id == job.job_id
        assert current.worker_id == lease.worker_id
        calls.append((current, submitted))

    accepted = await service.finish(lease, result(lease), verified)
    assert accepted.status == TrialStatus.COMPLETED and accepted.result.rewards == {"reward": 0.0}
    assert await service.finish(lease, result(lease), unavailable) == accepted
    assert len(calls) == 2


async def test_ownership_rejected_before_verification_and_expiry_rechecked_after(engine):
    service = ExecutionService(engine)
    job = await service.create_job(request())
    lease = await service.claim(uuid4(), lease_seconds=1, runnable_only=True)
    calls = []

    async def verified(current, _submitted):
        calls.append(current)

    with pytest.raises(LeaseRejected):
        await service.finish(
            lease.model_copy(update={"worker_id": uuid4()}), result(lease), verified
        )
    assert calls == []

    async def expires_during_verification(_current, _submitted):
        await asyncio.sleep(1.1)

    with pytest.raises(LeaseRejected):
        await service.finish(lease, result(lease), expires_during_verification)
    with pytest.raises(LeaseRejected):
        await service.finish(lease, result(lease), verified)
    assert calls == []
    assert (await service.get_job(job.job_id, job.owner_id)).trial.status == TrialStatus.RUNNING


async def test_real_minio_and_authenticated_hub_verify_before_commit(engine, server, store, output):
    import hashlib
    import json
    import secrets

    import httpx
    from pydantic import SecretStr
    from rollforge_api.main import create_app
    from rollforge_common.settings import Settings
    from rollforge_hub_sdk.client import HubClient, HubError
    from rollforge_object_store.bundle import BundlePublisher
    from rollforge_schemas.api import ClaimRequest, FinishRequest
    from rollforge_schemas.storage import ExecutionScope
    from rollforge_worker.executor import reference

    worker_id = uuid4()
    token = secrets.token_urlsafe(32)
    settings = Settings(
        _env_file=None,
        control_plane_writes_enabled=True,
        object_storage_endpoint=server.endpoint,
        object_storage_bucket=store.bucket,
        object_storage_access_key=server.access,
        object_storage_secret_key=server.secret,
        object_storage_allow_local_http=True,
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
    service = ExecutionService(engine)
    job = await service.create_job(request())
    app = create_app(settings, engine=engine)
    async with HubClient(
        "http://hub.test", SecretStr(token), transport=httpx.ASGITransport(app=app)
    ) as hub:
        lease = await hub.claim(ClaimRequest(runnable_only=True))
        scope = ExecutionScope(
            job_id=lease.job_id,
            trial_id=lease.trial_id,
            execution_id=lease.execution_id,
            fencing_token=lease.fencing_token,
        )
        # 对象不存在时返回 503 且状态不变。
        with pytest.raises(HubError) as missing:
            await hub.finish(FinishRequest(lease=reference(lease), result=result(lease)))
        assert missing.value.status_code == 503
        accepted_result = BundlePublisher(store, sensitive_values=()).publish(scope, output)
        forged = accepted_result.model_copy(update={"rewards": {"reward": 1.0}})
        with pytest.raises(HubError) as conflict:
            await hub.finish(FinishRequest(lease=reference(lease), result=forged))
        assert conflict.value.status_code == 503
        assert (await service.get_job(job.job_id, job.owner_id)).trial.status == TrialStatus.RUNNING
        body = FinishRequest(lease=reference(lease), result=accepted_result)
        accepted = await hub.finish(body)
        assert accepted.status == TrialStatus.COMPLETED and accepted.result.rewards == {"reward": 0}
        # 已接受但客户端未收到响应的恢复不依赖存储再次可用。
        store.client.delete_object(Bucket=store.bucket, Key=scope.manifest_key)
        assert await hub.finish(body) == accepted
