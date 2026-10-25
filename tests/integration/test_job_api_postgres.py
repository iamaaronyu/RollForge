import hashlib
import json
import secrets
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from rollforge_api.main import create_app
from rollforge_api.models import Execution
from rollforge_common.settings import Settings
from rollforge_hub_sdk.client import HubClient, HubError
from rollforge_schemas.api import (
    ClaimRequest,
    FinishRequest,
    JobCreateRequest,
    LeaseReference,
    RenewRequest,
)
from rollforge_schemas.domain import RevisionRef, TrialStatus
from rollforge_schemas.execution import ExecutionSnapshot, ResultCommit
from sqlalchemy import func, text, update


@asynccontextmanager
async def clients(engine, writes=True):
    identities = {name: uuid4() for name in ("user", "other_user", "worker", "other_worker")}
    tokens = {name: secrets.token_urlsafe(32) for name in identities}
    settings = Settings(
        _env_file=None,
        control_plane_writes_enabled=writes,
        api_credentials=SecretStr(
            json.dumps(
                [
                    {
                        "subject_id": str(identities[name]),
                        "role": "WORKER" if "worker" in name else "USER",
                        "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                    }
                    for name, token in tokens.items()
                ]
            )
        ),
    )
    app = create_app(settings, engine=engine)
    transport = httpx.ASGITransport(app=app)
    sdk = {
        name: HubClient("http://hub.test", SecretStr(token), transport=transport)
        for name, token in tokens.items()
    }
    raw = httpx.AsyncClient(base_url="http://hub.test", transport=transport)
    try:
        yield app, sdk, raw, identities, tokens
    finally:
        for client in sdk.values():
            await client.close()
        await raw.aclose()


def job_request():
    refs = [RevisionRef(id=uuid4(), revision=1, digest="sha256:" + "a" * 64) for _ in range(3)]
    return JobCreateRequest(
        job_id=uuid4(),
        snapshot=ExecutionSnapshot(
            task=refs[0],
            agent=refs[1],
            model=refs[2],
        ),
    )


def reference(lease):
    return LeaseReference.model_validate(
        lease.model_dump(
            include={"trial_id", "execution_id", "fencing_token"},
        )
    )


def commit(job, lease):
    return FinishRequest(
        lease=reference(lease),
        result=ResultCommit(
            outcome="SCORED",
            rewards={"reward": 0},
            manifest_key=f"jobs/{job.job_id}/trials/{lease.trial_id}/executions/{lease.execution_id}/manifest.json",
            manifest_digest="sha256:" + "a" * 64,
        ),
    )


async def test_sdk_database_roundtrip_and_idempotency(engine):
    async with clients(engine) as (_, sdk, _, identities, _):
        body = job_request()
        job = await sdk["user"].create_job(body)
        assert job.owner_id == identities["user"]
        assert await sdk["user"].create_job(body) == job
        lease = await sdk["worker"].claim(ClaimRequest())
        assert lease.worker_id == identities["worker"]
        assert await sdk["worker"].claim() is None
        renewed = await sdk["worker"].renew(RenewRequest(lease=reference(lease)))
        assert renewed.expires_at >= lease.expires_at
        finished = await sdk["worker"].finish(commit(job, lease))
        assert finished.status == TrialStatus.COMPLETED
        assert await sdk["worker"].finish(commit(job, lease)) == finished
        assert (await sdk["user"].get_job(job.job_id)).trial == finished


async def test_cross_user_and_worker_access_is_rejected(engine):
    async with clients(engine) as (_, sdk, raw, _, tokens):
        job = await sdk["user"].create_job(job_request())
        with pytest.raises(HubError) as exc:
            await sdk["other_user"].get_job(job.job_id)
        assert exc.value.status_code == 404
        lease = await sdk["worker"].claim()
        for operation in (
            sdk["other_worker"].renew(RenewRequest(lease=reference(lease))),
            sdk["other_worker"].finish(commit(job, lease)),
        ):
            with pytest.raises(HubError) as exc:
                await operation
            assert exc.value.status_code == 409 and exc.value.code == "LEASE_REJECTED"
        with pytest.raises(HubError) as exc:
            await sdk["user"].claim()
        assert exc.value.status_code == 403
        with pytest.raises(HubError) as exc:
            await sdk["worker"].get_job(job.job_id)
        assert exc.value.status_code == 403
        forged = commit(job, lease).model_dump(mode="json")
        forged["lease"]["worker_id"] = str(lease.worker_id)
        forged["credential"] = tokens["worker"]
        response = await raw.post(
            "/api/v1/worker/leases/finish",
            json=forged,
            headers={"Authorization": "Bearer " + tokens["other_worker"]},
        )
        assert response.status_code == 422 and tokens["worker"] not in response.text
        forged_job = job_request().model_dump(mode="json")
        forged_job["owner_id"] = str(job.owner_id)
        response = await raw.post(
            "/api/v1/jobs",
            json=forged_job,
            headers={"Authorization": "Bearer " + tokens["other_user"]},
        )
        assert response.status_code == 422
        marker = secrets.token_urlsafe(32)
        response = await raw.get(
            f"/api/v1/jobs/{marker}", headers={"Authorization": "Bearer " + tokens["user"]}
        )
        assert response.status_code == 422 and marker not in response.text


async def test_api_conflict_expiration_validation_and_closed_gate(engine):
    async with clients(engine) as (_, sdk, raw, _, tokens):
        body = job_request()
        job = await sdk["user"].create_job(body)
        changed = body.model_copy(
            update={"snapshot": body.snapshot.model_copy(update={"timeout_sec": 600})}
        )
        with pytest.raises(HubError) as exc:
            await sdk["user"].create_job(changed)
        assert exc.value.status_code == 409 and exc.value.code == "CONFLICT"
        response = await raw.post(
            "/api/v1/worker/leases/claim",
            json={"lease_seconds": 0},
            headers={"Authorization": "Bearer " + tokens["worker"]},
        )
        assert response.status_code == 422
        lease = await sdk["worker"].claim()
        async with engine.begin() as connection:
            await connection.execute(
                update(Execution)
                .where(Execution.id == lease.execution_id)
                .values(expires_at=func.clock_timestamp())
            )
        with pytest.raises(HubError) as exc:
            await sdk["worker"].finish(commit(job, lease))
        assert exc.value.status_code == 409
    async with clients(engine, writes=False) as (_, sdk, _, _, _):
        for operation in (sdk["user"].create_job(job_request()), sdk["worker"].claim()):
            with pytest.raises(HubError) as exc:
                await operation
            assert exc.value.status_code == 503 and exc.value.code == "WRITES_DISABLED"


async def test_openapi_declares_security_and_server_owned_identities(engine):
    async with clients(engine) as (app, _, raw, _, _):
        schema = (await raw.get("/openapi.json")).json()
        for path, methods in schema["paths"].items():
            if path == "/api/v1/jobs" or "/jobs/" in path or "/worker/" in path:
                for operation in methods.values():
                    assert operation["security"] == [{"HTTPBearer": []}]
        schemas = schema["components"]["schemas"]
        assert "owner_id" not in schemas["JobCreateRequest"]["properties"]
        assert "worker_id" not in schemas["LeaseReference"]["properties"]
        assert app.state.settings.control_plane_writes_enabled is True
        assert (await raw.get("/api/v1/platform")).json()["execution_enabled"] is False


async def test_database_failure_is_sanitized_and_claim_rolls_back(engine):
    async with clients(engine) as (_, sdk, raw, _, tokens):
        job = await sdk["user"].create_job(job_request())
        # 只在本测试的独立 schema 注入故障，触发领取事务中的数据库错误。
        async with engine.begin() as connection:
            await connection.execute(text("DROP TABLE executions"))
        response = await raw.post(
            "/api/v1/worker/leases/claim",
            json={},
            headers={"Authorization": "Bearer " + tokens["worker"]},
        )
        assert response.status_code == 503
        assert response.json()["code"] == "DATABASE_UNAVAILABLE"
        assert "INSERT" not in response.text and tokens["worker"] not in response.text
        async with engine.connect() as connection:
            status = await connection.scalar(
                text("SELECT status FROM trials WHERE job_id = :job_id"),
                {"job_id": job.job_id},
            )
        assert status == TrialStatus.QUEUED


async def test_multi_trial_expansion_pagination_and_retry_identity(engine):
    from datetime import timedelta

    from rollforge_api.execution_service import ExecutionService

    async with clients(engine) as (_, sdk, raw, _ids, tokens):
        body = job_request().model_copy(update={"trial_count": 3})
        job = await sdk["user"].create_job(body)
        assert job.trial_count == 3 and job.terminal_trial_count == 0
        assert await sdk["user"].create_job(body) == job
        summary = (await sdk["user"].list_jobs(limit=1)).items[0]
        assert summary.job_id == job.job_id and summary.trial_count == 3
        assert summary.terminal_trial_count == 0
        changed = body.model_copy(update={"trial_count": 2})
        with pytest.raises(HubError) as error:
            await sdk["user"].create_job(changed)
        assert error.value.status_code == 409

        first_page = await sdk["user"].list_trials(job.job_id, limit=2)
        assert [trial.attempt_index for trial in first_page.items] == [0, 1]
        assert first_page.next_cursor == 1
        last_page = await sdk["user"].list_trials(job.job_id, after=first_page.next_cursor)
        assert [trial.attempt_index for trial in last_page.items] == [2]
        assert last_page.next_cursor is None
        assert len({trial.trial_id for trial in first_page.items + last_page.items}) == 3

        headers = {"Authorization": "Bearer " + tokens["other_user"]}
        assert (
            await raw.get(f"/api/v1/jobs/{job.job_id}/trials", headers=headers)
        ).status_code == 404

        leases = [await sdk["worker"].claim() for _ in range(3)]
        assert [lease.snapshot for lease in leases] == [body.snapshot] * 3
        assert len({lease.trial_id for lease in leases}) == 3
        assert (await sdk["worker"].finish(commit(job, leases[0]))).attempt_index == 0

        history = await sdk["user"].list_executions(job.job_id, limit=1)
        assert history.items[0].attempt_index == 0 and history.next_cursor == 1
        second = await sdk["user"].list_executions(job.job_id, limit=1, after=history.next_cursor)
        assert second.items[0].attempt_index == 1 and second.items[0].execution_sequence == 2
        assert second.next_cursor == 2
        third = await sdk["user"].list_executions(job.job_id, limit=1, after=second.next_cursor)
        assert third.items[0].attempt_index == 2 and third.items[0].execution_sequence == 3
        assert third.next_cursor is None

        async with engine.begin() as connection:
            await connection.execute(
                update(Execution)
                .where(Execution.id == leases[1].execution_id)
                .values(expires_at=func.clock_timestamp() - timedelta(seconds=1))
            )
        assert await ExecutionService(engine).recover_expired() == 1
        retried = await sdk["worker"].claim()
        assert retried.trial_id == leases[1].trial_id
        assert retried.fencing_token == 2
        after_retry = await sdk["user"].list_executions(job.job_id, limit=2, after=3)
        assert [(item.attempt_index, item.fencing_token) for item in after_retry.items] == [
            (1, 2),
        ]
        assert after_retry.items[0].execution_sequence == 4
        refreshed = await sdk["user"].get_job(job.job_id)
        assert refreshed.trial_count == 3 and refreshed.terminal_trial_count == 1
