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
        assert (await sdk["user"].get_job(job.job_id)).trial.status == TrialStatus.QUEUED
