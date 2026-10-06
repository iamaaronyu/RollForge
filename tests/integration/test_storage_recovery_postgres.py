"""真实 MinIO/PostgreSQL 与鉴权 Hub 联合恢复，不创建 Sandbox 或调用模型。"""

import hashlib
import json
import secrets
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
import test_object_store_minio as storage_tests
from pydantic import SecretStr
from rollforge_api.execution_service import ExecutionService
from rollforge_api.main import create_app
from rollforge_api.models import Execution
from rollforge_common.settings import Settings
from rollforge_hub_sdk.client import HubClient, HubError, HubTransportError
from rollforge_object_store.bundle import BundlePublisher
from rollforge_schemas.api import ClaimRequest, ErrorCode, FinishRequest, LeaseReference
from rollforge_schemas.domain import RevisionRef, TrialStatus
from rollforge_schemas.execution import CreateJob, ExecutionSnapshot
from rollforge_schemas.runnable import RunnableBinding
from rollforge_schemas.storage import ExecutionScope
from sqlalchemy import func, select, update

server = storage_tests.server
store = storage_tests.store
output = storage_tests.output


def configuration(server, store):
    token = SecretStr(secrets.token_urlsafe(32))
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
                        "subject_id": str(uuid4()),
                        "role": "WORKER",
                        "token_sha256": hashlib.sha256(
                            token.get_secret_value().encode()
                        ).hexdigest(),
                    }
                ]
            )
        ),
    )
    return settings, token


def job_request():
    runtime = RunnableBinding(
        task_archive_key="tasks/recovery-test.tar.gz",
        task_archive_digest="sha256:" + "a" * 64,
        model_base_url="https://gateway.test",
    )
    return CreateJob(
        job_id=uuid4(),
        owner_id=uuid4(),
        snapshot=ExecutionSnapshot(
            task=RevisionRef(id=uuid4(), revision=1, digest="sha256:" + "a" * 64),
            agent=RevisionRef(id=uuid4(), revision=1, digest=runtime.agent_digest),
            model=RevisionRef(id=uuid4(), revision=1, digest=runtime.model_digest),
            runtime=runtime,
        ),
    )


def scope_of(lease):
    return ExecutionScope(
        job_id=lease.job_id,
        trial_id=lease.trial_id,
        execution_id=lease.execution_id,
        fencing_token=lease.fencing_token,
    )


def submission(lease, result):
    return FinishRequest(
        lease=LeaseReference(
            trial_id=lease.trial_id,
            execution_id=lease.execution_id,
            fencing_token=lease.fencing_token,
        ),
        result=result,
    )


async def expire(engine, lease):
    async with engine.begin() as connection:
        await connection.execute(
            update(Execution)
            .where(Execution.id == lease.execution_id)
            .values(expires_at=func.clock_timestamp() - timedelta(seconds=1))
        )


class LoseAcceptedResponse(httpx.AsyncBaseTransport):
    def __init__(self, app):
        self.inner = httpx.ASGITransport(app=app)
        self.lost = False

    async def handle_async_request(self, request):
        response = await self.inner.handle_async_request(request)
        if request.url.path.endswith("/finish") and response.status_code == 200 and not self.lost:
            self.lost = True
            await response.aclose()
            raise httpx.ReadError("注入 Hub 已提交后的响应丢失", request=request)
        return response

    async def aclose(self):
        await self.inner.aclose()


async def test_accepted_response_loss_recovers_after_hub_restart_without_manifest(
    engine, server, store, output
):
    settings, token = configuration(server, store)
    service = ExecutionService(engine)
    job = await service.create_job(job_request())
    transport = LoseAcceptedResponse(create_app(settings, engine=engine))
    async with HubClient("http://hub.test", token, transport=transport) as hub:
        lease = await hub.claim(ClaimRequest(runnable_only=True))
        published = BundlePublisher(store, sensitive_values=()).publish(scope_of(lease), output)
        body = submission(lease, published)
        with pytest.raises(HubTransportError):
            await hub.finish(body)
        assert transport.lost
    accepted = (await service.get_job(job.job_id, job.owner_id)).trial
    assert accepted.status == TrialStatus.COMPLETED and accepted.result == published
    await expire(engine, lease)
    store.client.delete_object(Bucket=store.bucket, Key=published.manifest_key)
    await engine.dispose()
    # 新应用实例和客户端必须从 PostgreSQL 返回已接受的相同结果。
    restarted = create_app(settings, engine=engine)
    async with HubClient(
        "http://hub.test", token, transport=httpx.ASGITransport(app=restarted)
    ) as hub:
        assert await hub.finish(body) == accepted
        with pytest.raises(HubError) as changed:
            await hub.finish(
                body.model_copy(
                    update={"result": published.model_copy(update={"rewards": {"reward": 1}})}
                )
            )
        assert changed.value.code == ErrorCode.CONFLICT


async def test_uploaded_old_execution_cannot_commit_after_retry(engine, server, store, output):
    settings, token = configuration(server, store)
    service = ExecutionService(engine)
    job = await service.create_job(job_request())
    publisher = BundlePublisher(store, sensitive_values=())
    async with HubClient(
        "http://hub.test",
        token,
        transport=httpx.ASGITransport(app=create_app(settings, engine=engine)),
    ) as hub:
        old = await hub.claim(ClaimRequest(runnable_only=True))
        old_result = publisher.publish(scope_of(old), output)
        await expire(engine, old)
        assert await service.recover_expired() == 1
        new = await hub.claim(ClaimRequest(runnable_only=True))
        assert new.trial_id == old.trial_id and new.execution_id != old.execution_id
        assert new.fencing_token == old.fencing_token + 1
        # 旧 Manifest 内容正确且仍能验证，但不能代表新的数据库执行权。
        assert publisher.verify(scope_of(old), old_result.manifest_digest) == old_result
        with pytest.raises(HubError) as stale:
            await hub.finish(submission(old, old_result))
        assert stale.value.status_code == 409 and stale.value.code == ErrorCode.LEASE_REJECTED
        assert (await service.get_job(job.job_id, job.owner_id)).trial.status == TrialStatus.RUNNING
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    select(Execution.result).where(Execution.id == old.execution_id)
                )
            ) is None
        new_result = publisher.publish(scope_of(new), output)
        assert new_result.manifest_key != old_result.manifest_key
        accepted = await hub.finish(submission(new, new_result))
        assert accepted.status == TrialStatus.COMPLETED and accepted.result.rewards == {"reward": 0}
        assert publisher.verify(scope_of(old), old_result.manifest_digest) == old_result
