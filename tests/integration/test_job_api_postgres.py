import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from asyncio import gather, sleep
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
    ApprovedTaskBinding,
    ClaimRequest,
    FinishRequest,
    JobCreateRequest,
    LeaseReference,
    RegistryJobCreateRequest,
    RenewRequest,
)
from rollforge_schemas.domain import RevisionRef, TrialStatus
from rollforge_schemas.execution import (
    ExecutionSnapshot,
    ExecutionStatus,
    RegistrySelection,
    ResultCommit,
)
from rollforge_schemas.registry import AgentSpec, ModelSpec, RevisionCreate, TaskSpec
from rollforge_schemas.runnable import RunnableBinding
from sqlalchemy import func, select, text, update


@asynccontextmanager
async def clients(engine, writes=True, approved_tasks_file=None, global_execution_limit=1):
    identities = {name: uuid4() for name in ("user", "other_user", "worker", "other_worker")}
    tokens = {name: secrets.token_urlsafe(32) for name in identities}
    settings = Settings(
        _env_file=None,
        control_plane_writes_enabled=writes,
        global_execution_limit=global_execution_limit,
        approved_tasks_file=approved_tasks_file,
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

    async with clients(engine, global_execution_limit=3) as (_, sdk, raw, _ids, tokens):
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


async def test_database_global_execution_limit_across_concurrent_claims(engine):
    async with clients(engine, global_execution_limit=2) as (_, sdk, _, _, _):
        job = await sdk["user"].create_job(job_request().model_copy(update={"trial_count": 5}))
        claims = await gather(*(sdk["worker"].claim() for _ in range(8)))
        leases = [lease for lease in claims if lease is not None]
        assert len(leases) == 2
        assert await sdk["worker"].claim() is None

        assert (await sdk["worker"].finish(commit(job, leases[0]))).status == TrialStatus.COMPLETED
        replacement = await sdk["worker"].claim()
        assert replacement is not None
        assert replacement.trial_id not in {lease.trial_id for lease in leases}


async def test_hundred_trial_job_paginates_and_claims_with_global_capacity(engine):
    async with clients(engine, global_execution_limit=100) as (_, sdk, _, _, _):
        body = job_request().model_copy(update={"trial_count": 100})
        job = await sdk["user"].create_job(body)
        assert job.trial_count == 100 and job.terminal_trial_count == 0
        assert await sdk["user"].create_job(body) == job

        trials = []
        cursor = None
        while True:
            page = await sdk["user"].list_trials(job.job_id, limit=37, after=cursor)
            trials.extend(page.items)
            cursor = page.next_cursor
            if cursor is None:
                break
        assert [trial.attempt_index for trial in trials] == list(range(100))
        assert len({trial.trial_id for trial in trials}) == 100

        claims = await gather(*(sdk["worker"].claim() for _ in range(100)))
        leases = [lease for lease in claims if lease is not None]
        assert len(leases) == 100
        assert {lease.trial_id for lease in leases} == {trial.trial_id for trial in trials}
        assert all(lease.snapshot == body.snapshot for lease in leases)
        assert await sdk["worker"].claim() is None


async def test_postgres_global_capacity_is_shared_across_processes(engine, tmp_path):
    async with clients(engine, global_execution_limit=1) as (_, sdk, _, _, _):
        await sdk["user"].create_job(job_request().model_copy(update={"trial_count": 2}))
        async with engine.connect() as connection:
            schema = await connection.scalar(text("SELECT current_schema()"))

        release = tmp_path / "release-claims"
        ready_dir = tmp_path / "ready"
        ready_dir.mkdir()
        script = """
import asyncio
import os
import time
from pathlib import Path
from uuid import UUID
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from rollforge_api.execution_service import ExecutionService

release = Path(os.environ["ROLLFORGE_PROCESS_TEST_RELEASE"])

async def main():
    worker_id = os.environ["ROLLFORGE_PROCESS_TEST_WORKER_ID"]
    engine = create_async_engine(
        os.environ["ROLLFORGE_TEST_DATABASE_URL"],
        connect_args={
            "server_settings": {"search_path": os.environ["ROLLFORGE_PROCESS_TEST_SCHEMA"]}
        },
    )
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        ready_path = Path(os.environ["ROLLFORGE_PROCESS_TEST_READY_DIR"]) / worker_id
        ready_path.touch()
        deadline = time.monotonic() + 15
        while not release.exists():
            if time.monotonic() >= deadline:
                raise SystemExit("claim barrier timed out")
            await asyncio.sleep(0.01)
        lease = await ExecutionService(engine, global_execution_limit=1).claim(
            UUID(worker_id)
        )
        print("CLAIMED" if lease else "EMPTY")
    finally:
        await engine.dispose()

asyncio.run(main())
"""
        processes = []
        for _ in range(2):
            env = os.environ.copy()
            env["ROLLFORGE_PROCESS_TEST_RELEASE"] = str(release)
            env["ROLLFORGE_PROCESS_TEST_SCHEMA"] = schema
            env["ROLLFORGE_PROCESS_TEST_READY_DIR"] = str(ready_dir)
            env["ROLLFORGE_PROCESS_TEST_WORKER_ID"] = str(uuid4())
            processes.append(
                subprocess.Popen(
                    [sys.executable, "-c", script],
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        deadline = time.monotonic() + 20
        while len(list(ready_dir.iterdir())) != len(processes):
            if time.monotonic() >= deadline:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                outputs = [process.communicate(timeout=5) for process in processes]
                raise AssertionError("claim processes failed to reach database barrier")
            await sleep(0.01)
        release.touch()
        try:
            outputs = [process.communicate(timeout=30) for process in processes]
        except subprocess.TimeoutExpired:
            for process in processes:
                if process.poll() is None:
                    process.kill()
            outputs = [process.communicate(timeout=5) for process in processes]
            raise AssertionError("claim processes failed to finish after barrier") from None
        assert [process.returncode for process in processes] == [0, 0], outputs
        assert sorted(stdout.strip() for stdout, _ in outputs) == ["CLAIMED", "EMPTY"]
        async with engine.connect() as connection:
            running = await connection.scalar(
                select(func.count(Execution.id)).where(Execution.status == ExecutionStatus.RUNNING)
            )
        assert running == 1


async def test_global_capacity_is_shared_by_two_hub_http_processes(engine):
    async with clients(engine, global_execution_limit=1) as (_, sdk, _, identities, tokens):
        await sdk["user"].create_job(job_request().model_copy(update={"trial_count": 2}))
        async with engine.connect() as connection:
            schema = await connection.scalar(text("SELECT current_schema()"))

        script = """
import hashlib
import json
import os
import uvicorn
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import create_async_engine
from rollforge_common.settings import Settings
from rollforge_api.main import create_app

worker_id = os.environ["ROLLFORGE_PROCESS_TEST_WORKER_ID"]
worker_token = os.environ["ROLLFORGE_PROCESS_TEST_TOKEN"]
settings = Settings(
    _env_file=None,
    database_url=SecretStr(os.environ["ROLLFORGE_TEST_DATABASE_URL"]),
    api_credentials=SecretStr(json.dumps([{
        "subject_id": worker_id,
        "role": "WORKER",
        "token_sha256": hashlib.sha256(worker_token.encode()).hexdigest(),
    }])),
    control_plane_writes_enabled=True,
    global_execution_limit=1,
)
engine = create_async_engine(
    settings.database_url.get_secret_value(),
    connect_args={"server_settings": {"search_path": os.environ["ROLLFORGE_PROCESS_TEST_SCHEMA"]}},
)
app = create_app(settings, engine=engine)
uvicorn.run(
    app,
    host="127.0.0.1",
    port=int(os.environ["ROLLFORGE_PROCESS_TEST_PORT"]),
    log_level="critical",
)
"""

        def unused_loopback_port():
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                return listener.getsockname()[1]

        workers = (("worker", identities["worker"]), ("other_worker", identities["other_worker"]))
        processes = []
        clients_by_process = []
        selected_ports = set()
        for name, worker_id in workers:
            port = unused_loopback_port()
            while port in selected_ports:
                port = unused_loopback_port()
            selected_ports.add(port)
            env = os.environ.copy()
            env["ROLLFORGE_PROCESS_TEST_SCHEMA"] = schema
            env["ROLLFORGE_PROCESS_TEST_PORT"] = str(port)
            env["ROLLFORGE_PROCESS_TEST_WORKER_ID"] = str(worker_id)
            env["ROLLFORGE_PROCESS_TEST_TOKEN"] = tokens[name]
            process = subprocess.Popen(
                [sys.executable, "-c", script],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            processes.append(process)
            clients_by_process.append(httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}"))

        try:
            deadline = time.monotonic() + 20
            ready = False
            while time.monotonic() < deadline:
                failed = next(
                    (process for process in processes if process.poll() is not None), None
                )
                if failed is not None:
                    error = failed.communicate(timeout=5)[1]
                    raise AssertionError(f"Hub HTTP process exited before readiness: {error}")
                responses = await gather(
                    *(client.get("/health/ready") for client in clients_by_process),
                    return_exceptions=True,
                )
                if all(
                    isinstance(response, httpx.Response) and response.status_code == 200
                    for response in responses
                ):
                    ready = True
                    break
                await sleep(0.05)
            assert ready, "both Hub HTTP processes must become ready"

            claims = await gather(
                *(
                    client.post(
                        "/api/v1/worker/leases/claim",
                        json={},
                        headers={"Authorization": "Bearer " + tokens[name]},
                    )
                    for (name, _worker_id), client in zip(workers, clients_by_process, strict=True)
                )
            )
            assert sorted(response.status_code for response in claims) == [200, 204]
            async with engine.connect() as connection:
                running = await connection.scalar(
                    select(func.count(Execution.id)).where(
                        Execution.status == ExecutionStatus.RUNNING
                    )
                )
            assert running == 1
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
            for process in processes:
                try:
                    process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)
            for client in clients_by_process:
                await client.aclose()


async def test_worker_recovery_api_requeues_only_expired_leases(engine):
    async with clients(engine) as (_, sdk, _, _, _):
        job = await sdk["user"].create_job(job_request())
        lease = await sdk["worker"].claim(ClaimRequest(lease_seconds=30))
        assert lease.trial_id == job.trial.trial_id
        assert (await sdk["worker"].recover_expired()).recovered_count == 0
        async with engine.begin() as connection:
            await connection.execute(
                update(Execution)
                .where(Execution.id == lease.execution_id)
                .values(expires_at=func.clock_timestamp())
            )
        assert (await sdk["worker"].recover_expired()).recovered_count == 1
        recovered = await sdk["user"].get_job(job.job_id)
        assert recovered.trial.status == TrialStatus.QUEUED
        replacement = await sdk["worker"].claim()
        assert replacement.trial_id == lease.trial_id
        assert replacement.fencing_token == lease.fencing_token + 1
        async with engine.connect() as connection:
            status = await connection.scalar(
                select(Execution.status).where(Execution.id == lease.execution_id)
            )
        assert status == ExecutionStatus.EXPIRED


async def test_registry_revisions_resolve_only_to_approved_runtime(engine, tmp_path):
    import json
    from pathlib import Path

    task_id, agent_id, model_id = uuid4(), uuid4(), uuid4()
    content_digest = "sha256:" + "e" * 64
    archive_digest = "sha256:" + "f" * 64
    binding = RunnableBinding(
        task_archive_key="tasks/reviewed.tar.gz",
        task_archive_digest=archive_digest,
        model_base_url="https://gateway.test",
    )
    approved_snapshot = ExecutionSnapshot(
        task=RevisionRef(id=task_id, revision=1, digest=content_digest),
        agent=RevisionRef(id=agent_id, revision=1, digest=binding.agent_digest),
        model=RevisionRef(id=model_id, revision=1, digest=binding.model_digest),
        runtime=binding,
    )
    catalog_path = Path(tmp_path) / "approved.json"
    catalog_path.write_text(
        json.dumps(
            [
                ApprovedTaskBinding(
                    id="reviewed-demo", label="审核样例", snapshot=approved_snapshot
                ).model_dump(mode="json")
            ]
        )
    )
    catalog_path.chmod(0o600)

    async with clients(engine, approved_tasks_file=catalog_path) as (_, sdk, _, _, _):
        task_revision = await sdk["user"].create_revision(
            RevisionCreate(
                asset_id=task_id,
                revision=1,
                spec=TaskSpec(archive_key=binding.task_archive_key, archive_digest=archive_digest),
            )
        )
        agent_revision = await sdk["user"].create_revision(
            RevisionCreate(asset_id=agent_id, revision=1, spec=AgentSpec())
        )
        model_revision = await sdk["user"].create_revision(
            RevisionCreate(
                asset_id=model_id,
                revision=1,
                spec=ModelSpec(model_base_url=binding.model_base_url),
            )
        )
        selection = RegistrySelection(
            task=RevisionRef(id=task_id, revision=1, digest=task_revision.digest),
            agent=RevisionRef(id=agent_id, revision=1, digest=agent_revision.digest),
            model=RevisionRef(id=model_id, revision=1, digest=model_revision.digest),
        )
        body = RegistryJobCreateRequest(job_id=uuid4(), revisions=selection, trial_count=2)
        job = await sdk["user"].create_job_from_revisions(body)
        assert job.trial_count == 2
        assert job.snapshot.runtime == binding
        assert job.snapshot.registry == selection
        assert job.snapshot.task.digest == content_digest
        assert await sdk["user"].create_job_from_revisions(body) == job

        with pytest.raises(HubError) as error:
            await sdk["other_user"].create_job_from_revisions(
                body.model_copy(update={"job_id": uuid4()})
            )
        assert error.value.status_code == 404

        invalid = body.model_copy(
            update={
                "revisions": selection.model_copy(
                    update={
                        "model": selection.model.model_copy(update={"digest": "sha256:" + "0" * 64})
                    }
                )
            }
        )
        with pytest.raises(HubError) as error:
            await sdk["user"].create_job_from_revisions(invalid)
        assert error.value.status_code == 409


async def test_registry_revision_cannot_expand_deployment_approval(engine):
    async with clients(engine) as (_, sdk, _, _, _):
        task_revision = await sdk["user"].create_revision(
            RevisionCreate(
                asset_id=uuid4(),
                revision=1,
                spec=TaskSpec(
                    archive_key="tasks/unreviewed.tar.gz",
                    archive_digest="sha256:" + "1" * 64,
                ),
            )
        )
        agent_revision = await sdk["user"].create_revision(
            RevisionCreate(asset_id=uuid4(), revision=1, spec=AgentSpec())
        )
        model_revision = await sdk["user"].create_revision(
            RevisionCreate(
                asset_id=uuid4(),
                revision=1,
                spec=ModelSpec(model_base_url="https://attacker.example"),
            )
        )
        body = RegistryJobCreateRequest(
            job_id=uuid4(),
            revisions=RegistrySelection(
                task=RevisionRef(
                    id=task_revision.asset_id, revision=1, digest=task_revision.digest
                ),
                agent=RevisionRef(
                    id=agent_revision.asset_id, revision=1, digest=agent_revision.digest
                ),
                model=RevisionRef(
                    id=model_revision.asset_id, revision=1, digest=model_revision.digest
                ),
            ),
        )
        with pytest.raises(HubError) as error:
            await sdk["user"].create_job_from_revisions(body)
        assert error.value.status_code == 403


async def test_user_cannot_create_runnable_job_with_unapproved_snapshot(engine):
    async with clients(engine) as (_, sdk, _, _, _):
        runtime = RunnableBinding(
            task_archive_key="tasks/unreviewed.tar.gz",
            task_archive_digest="sha256:" + "1" * 64,
            model_base_url="https://gateway.test",
        )
        snapshot = ExecutionSnapshot(
            task=RevisionRef(id=uuid4(), revision=1, digest="sha256:" + "2" * 64),
            agent=RevisionRef(id=uuid4(), revision=1, digest=runtime.agent_digest),
            model=RevisionRef(id=uuid4(), revision=1, digest=runtime.model_digest),
            runtime=runtime,
        )
        with pytest.raises(HubError) as error:
            await sdk["user"].create_job(JobCreateRequest(job_id=uuid4(), snapshot=snapshot))
        assert error.value.status_code == 403


async def test_cancel_job_is_idempotent_scoped_and_cancels_queued_trials(engine):
    async with clients(engine) as (_, sdk, _, identities, _):
        body = job_request().model_copy(update={"trial_count": 3})
        job = await sdk["user"].create_job(body)
        cancelled = await sdk["user"].cancel_job(job.job_id)
        assert cancelled.terminal_trial_count == 3
        trials = await sdk["user"].list_trials(job.job_id, limit=10)
        assert [trial.status for trial in trials.items] == [TrialStatus.CANCELLED] * 3
        assert await sdk["user"].cancel_job(job.job_id) == cancelled

        with pytest.raises(HubError) as error:
            await sdk["other_user"].cancel_job(job.job_id)
        assert error.value.status_code == 404
        with pytest.raises(HubError) as error:
            await sdk["worker"].cancel_job(job.job_id)
        assert error.value.status_code == 403

    async with clients(engine, writes=False) as (_, sdk, _, _, _):
        # A fresh job is not needed to prove the shared write gate rejects the route.
        with pytest.raises(HubError) as error:
            await sdk["user"].cancel_job(uuid4())
        assert error.value.status_code == 503


async def test_cancel_running_trial_revokes_worker_lease_and_preserves_siblings(engine):
    async with clients(engine) as (_, sdk, _, _, _):
        job = await sdk["user"].create_job(job_request().model_copy(update={"trial_count": 2}))
        lease = await sdk["worker"].claim(ClaimRequest(lease_seconds=30))
        cancelled = await sdk["user"].cancel_trial(job.job_id, lease.trial_id)
        assert cancelled.status == TrialStatus.CANCELLED
        with pytest.raises(HubError) as error:
            await sdk["worker"].renew(RenewRequest(lease=reference(lease)))
        assert error.value.status_code == 409
        with pytest.raises(HubError) as error:
            await sdk["worker"].finish(commit(job, lease))
        assert error.value.status_code == 409

        executions = await sdk["user"].list_executions(job.job_id)
        assert len(executions.items) == 1
        assert executions.items[0].status == ExecutionStatus.CANCELLED
        assert (await sdk["worker"].recover_expired()).recovered_count == 0
        next_lease = await sdk["worker"].claim()
        assert next_lease.trial_id != lease.trial_id
        assert (await sdk["user"].get_job(job.job_id)).terminal_trial_count == 1


async def test_cancel_and_finish_race_has_one_terminal_winner(engine):
    async with clients(engine) as (_, sdk, _, _, _):
        job = await sdk["user"].create_job(job_request())
        lease = await sdk["worker"].claim()
        cancellation, completion = await gather(
            sdk["user"].cancel_job(job.job_id),
            sdk["worker"].finish(commit(job, lease)),
            return_exceptions=True,
        )
        assert sum(not isinstance(item, Exception) for item in (cancellation, completion)) == 1
        rejected = cancellation if isinstance(cancellation, Exception) else completion
        assert isinstance(rejected, HubError) and rejected.status_code == 409
        final = await sdk["user"].get_job(job.job_id)
        assert final.trial.status in {TrialStatus.CANCELLED, TrialStatus.COMPLETED}


async def test_cancel_completed_job_conflicts_but_partial_job_cancels_remaining_trials(engine):
    async with clients(engine) as (_, sdk, _, _, _):
        job = await sdk["user"].create_job(job_request().model_copy(update={"trial_count": 3}))
        lease = await sdk["worker"].claim()
        completed = await sdk["worker"].finish(commit(job, lease))
        assert completed.status == TrialStatus.COMPLETED

        cancelled = await sdk["user"].cancel_job(job.job_id)
        trials = await sdk["user"].list_trials(job.job_id, limit=10)
        assert [trial.status for trial in trials.items] == [
            TrialStatus.COMPLETED,
            TrialStatus.CANCELLED,
            TrialStatus.CANCELLED,
        ]
        assert await sdk["user"].cancel_job(job.job_id) == cancelled

        with pytest.raises(HubError) as error:
            await sdk["user"].cancel_trial(job.job_id, completed.trial_id)
        assert error.value.status_code == 409

        only_completed = await sdk["user"].create_job(job_request())
        only_lease = await sdk["worker"].claim()
        await sdk["worker"].finish(commit(only_completed, only_lease))
        with pytest.raises(HubError) as error:
            await sdk["user"].cancel_job(only_completed.job_id)
        assert error.value.status_code == 409
