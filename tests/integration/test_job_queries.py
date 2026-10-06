"""真实 PostgreSQL 的页面查询与审核目录创建，不调用模型。"""

from datetime import timedelta
from uuid import uuid4

import pytest
from rollforge_api.execution_service import ExecutionService
from rollforge_api.models import Execution
from rollforge_hub_sdk.client import HubError
from rollforge_schemas.api import ApprovedTaskBinding
from rollforge_schemas.domain import RevisionRef
from rollforge_schemas.runnable import RunnableBinding
from sqlalchemy import func, update
from test_job_api_postgres import clients, job_request


async def test_job_list_owner_pagination_and_role(engine):
    async with clients(engine) as (_app, sdk, _raw, _ids, _tokens):
        jobs = [await sdk["user"].create_job(job_request()) for _ in range(3)]
        foreign = await sdk["other_user"].create_job(job_request())
        found = []
        cursor = None
        while True:
            page = await sdk["user"].list_jobs(limit=1, after=cursor)
            found.extend(item.job_id for item in page.items)
            assert all("snapshot" not in item.model_dump() for item in page.items)
            cursor = page.next_cursor
            if cursor is None:
                break
        assert found == sorted(job.job_id for job in jobs)
        assert foreign.job_id not in found
        for invalid in (0, 101):
            with pytest.raises(HubError) as error:
                await sdk["user"].list_jobs(limit=invalid)
            assert error.value.status_code == 422
        with pytest.raises(HubError) as role:
            await sdk["worker"].list_jobs()
        assert role.value.status_code == 403


async def test_execution_history_owner_pagination_and_no_worker_identity(engine):
    async with clients(engine) as (_app, sdk, _raw, _ids, _tokens):
        job = await sdk["user"].create_job(job_request())
        assert not (await sdk["user"].list_executions(job.job_id)).items
        old = await sdk["worker"].claim()
        async with engine.begin() as connection:
            await connection.execute(
                update(Execution)
                .where(Execution.id == old.execution_id)
                .values(expires_at=func.clock_timestamp() - timedelta(seconds=1))
            )
        assert await ExecutionService(engine).recover_expired() == 1
        new = await sdk["worker"].claim()
        page = await sdk["user"].list_executions(job.job_id, limit=1)
        assert page.items[0].execution_id == old.execution_id
        assert page.items[0].status == "EXPIRED" and page.next_cursor == 1
        final = await sdk["user"].list_executions(job.job_id, after=page.next_cursor)
        assert final.items[0].execution_id == new.execution_id and final.next_cursor is None
        assert "worker_id" not in final.items[0].model_dump()
        for client, job_id in ((sdk["other_user"], job.job_id), (sdk["user"], uuid4())):
            with pytest.raises(HubError) as missing:
                await client.list_executions(job_id)
            assert missing.value.status_code == 404


async def test_approved_task_selection_uses_server_snapshot(engine):
    async with clients(engine) as (app, sdk, raw, _ids, tokens):
        binding = RunnableBinding(
            task_archive_key="tasks/approved.tar.gz",
            task_archive_digest="sha256:" + "a" * 64,
            model_base_url="https://gateway.test",
        )
        snapshot = job_request().snapshot.model_copy(
            update={
                "runtime": binding,
                "agent": RevisionRef(id=uuid4(), revision=1, digest=binding.agent_digest),
                "model": RevisionRef(id=uuid4(), revision=1, digest=binding.model_digest),
            }
        )
        app.state.approved_tasks = {
            "sample": ApprovedTaskBinding(id="sample", label="审核样例", snapshot=snapshot)
        }
        headers = {"Authorization": "Bearer " + tokens["user"]}
        options = await raw.get("/api/v1/tasks/approved", headers=headers)
        assert options.json() == {"items": [{"id": "sample", "label": "审核样例"}]}
        body = {"job_id": str(uuid4()), "task_id": "sample"}
        first = await raw.post("/api/v1/jobs/from-approved-task", headers=headers, json=body)
        assert first.status_code == 200
        assert first.json()["snapshot"] == snapshot.model_dump(mode="json")
        duplicate = await raw.post("/api/v1/jobs/from-approved-task", headers=headers, json=body)
        assert duplicate.json() == first.json()
        assert (
            await raw.post(
                "/api/v1/jobs/from-approved-task", headers=headers, json={**body, "snapshot": {}}
            )
        ).status_code == 422
        assert (
            await raw.post(
                "/api/v1/jobs/from-approved-task",
                headers=headers,
                json={**body, "task_id": "missing"},
            )
        ).status_code == 404
