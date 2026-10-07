"""真实 PostgreSQL 注册表竞争、所有权与数据库冻结。"""

import asyncio
from uuid import uuid4

import pytest
from rollforge_api.models import RegistryAsset, RegistryRevision
from rollforge_hub_sdk.client import HubError
from rollforge_schemas.registry import (
    AgentSpec,
    ModelSpec,
    RevisionCreate,
    TaskSpec,
    revision_digest,
)
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError
from test_job_api_postgres import clients


def task(asset_id=None, revision=1, digest="a"):
    return RevisionCreate(
        asset_id=asset_id or uuid4(),
        revision=revision,
        spec=TaskSpec(archive_key="tasks/synthetic.tar.gz", archive_digest="sha256:" + digest * 64),
    )


async def test_registry_competing_identical_and_changed_writes(engine):
    async with clients(engine) as (_, sdk, _raw, _ids, _tokens):
        body = task()
        first, duplicate = await asyncio.gather(
            *[sdk["user"].create_revision(body) for _ in range(2)]
        )
        assert first == duplicate and first.digest == revision_digest(body.spec)
        assert await sdk["user"].get_revision(body.asset_id, 1) == first

        async def changed(body):
            try:
                return await sdk["user"].create_revision(body)
            except HubError as error:
                return error.status_code

        values = await asyncio.gather(
            changed(task(body.asset_id, 2, "b")), changed(task(body.asset_id, 2, "c"))
        )
        assert sum(value == 409 for value in values) == 1
        assert sum(not isinstance(value, int) for value in values) == 1
        assert await sdk["user"].get_revision(body.asset_id, 1) == first
        with pytest.raises(HubError) as error:
            await sdk["user"].create_revision(task(body.asset_id, 1, "d"))
        assert error.value.status_code == 409
        with pytest.raises(HubError) as error:
            await sdk["user"].create_revision(task(body.asset_id, 4))
        assert error.value.status_code == 409
        page = await sdk["user"].list_revisions(body.asset_id, limit=1)
        assert page.next_cursor == 1 and page.items == (first,)
        last = await sdk["user"].list_revisions(body.asset_id, after=page.next_cursor)
        assert last.next_cursor is None and last.items[0].revision == 2


async def test_registry_owner_roles_kind_and_empty_gap_rollback(engine):
    async with clients(engine) as (_, sdk, raw, _ids, tokens):
        body = task()
        await sdk["user"].create_revision(body)
        for name, expected in [("other_user", 404), ("worker", 403)]:
            with pytest.raises(HubError) as error:
                await sdk[name].get_revision(body.asset_id, 1)
            assert error.value.status_code == expected
            with pytest.raises(HubError) as error:
                await sdk[name].create_revision(body)
            assert error.value.status_code == expected
            with pytest.raises(HubError) as error:
                await sdk[name].list_revisions(body.asset_id)
            assert error.value.status_code == expected
        with pytest.raises(HubError) as error:
            await sdk["user"].create_revision(
                RevisionCreate(asset_id=body.asset_id, revision=2, spec=AgentSpec())
            )
        assert error.value.status_code == 409
        skipped = task(revision=2)
        with pytest.raises(HubError):
            await sdk["user"].create_revision(skipped)
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    select(RegistryAsset.id).where(RegistryAsset.id == skipped.asset_id)
                )
                is None
            )
        payload = body.model_dump(mode="json")
        payload["spec"]["api_key"] = "synthetic-sensitive-input"
        response = await raw.post(
            "/api/v1/registry/revisions",
            json=payload,
            headers={"Authorization": "Bearer " + tokens["user"]},
        )
        assert response.status_code == 422
        assert "synthetic-sensitive-input" not in response.text
        for spec in [AgentSpec(), ModelSpec(model_base_url="https://gateway.test")]:
            created = await sdk["user"].create_revision(
                RevisionCreate(asset_id=uuid4(), revision=1, spec=spec)
            )
            assert created.spec == spec


async def test_registry_database_immutability(engine):
    async with clients(engine) as (_, sdk, _raw, _ids, _tokens):
        body = task()
        await sdk["user"].create_revision(body)
        statements = [
            update(RegistryRevision).values(spec={}),
            delete(RegistryRevision),
            update(RegistryAsset).values(owner_id=uuid4()),
            delete(RegistryAsset),
            text("TRUNCATE registry_revisions"),
            text("TRUNCATE registry_assets CASCADE"),
        ]
        for statement in statements:
            with pytest.raises(IntegrityError):
                async with engine.begin() as connection:
                    await connection.execute(statement)
        assert (await sdk["user"].get_revision(body.asset_id, 1)).spec == body.spec


async def test_registry_write_gate_and_bounds(engine):
    async with clients(engine, writes=False) as (_, sdk, raw, _ids, tokens):
        with pytest.raises(HubError) as error:
            await sdk["user"].create_revision(task())
        assert error.value.status_code == 503
        headers = {"Authorization": "Bearer " + tokens["user"]}
        base = f"/api/v1/registry/assets/{uuid4()}/revisions"
        for suffix in ["?limit=101", "?after=-1", "/0"]:
            assert (await raw.get(base + suffix, headers=headers)).status_code == 422
        assert (await raw.get(base)).status_code == 401


async def test_registry_migration_rollback_preserves_existing_jobs(engine):
    from alembic import command
    from alembic.config import Config
    from test_execution_postgres import ROOT
    from test_job_api_postgres import job_request

    async with clients(engine) as (_, sdk, _raw, _ids, _tokens):
        job = await sdk["user"].create_job(job_request())
        await sdk["user"].create_revision(task())

        def migrate(connection, target):
            config = Config(str(ROOT / "apps/hub-api/alembic.ini"))
            config.attributes["connection"] = connection
            if target == "head":
                command.upgrade(config, target)
            else:
                command.downgrade(config, target)

        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: migrate(sync, "0001_execution"))
            assert await connection.scalar(text("SELECT to_regclass('registry_revisions')")) is None
            assert await connection.scalar(text("SELECT count(*) FROM jobs")) == 1
            assert (
                await connection.scalar(text("SELECT to_regproc('reject_registry_mutation')"))
                is None
            )
            await connection.run_sync(lambda sync: migrate(sync, "head"))
        assert (await sdk["user"].get_job(job.job_id)).job_id == job.job_id
        created = await sdk["user"].create_revision(task())
        assert created.revision == 1
