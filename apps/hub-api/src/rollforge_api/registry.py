"""PostgreSQL 版本冻结；注册不会授予执行许可。"""

from typing import Annotated
from uuid import UUID

from fastapi import Path, Query, Request
from rollforge_schemas.api import ErrorCode, RegistryJobCreateRequest
from rollforge_schemas.domain import RevisionRef
from rollforge_schemas.execution import CreateJob, ExecutionSnapshot, JobView, RegistrySelection
from rollforge_schemas.registry import (
    AgentSpec,
    AssetRevision,
    ModelSpec,
    RevisionCreate,
    RevisionList,
    TaskSpec,
    revision_digest,
)
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from rollforge_api.models import RegistryAsset, RegistryRevision
from rollforge_api.routes import ApiFailure, Store, User, WriteGate, router


class Registry:
    def __init__(self, engine):
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)

    @staticmethod
    def view(row):
        return AssetRevision(
            asset_id=row.asset_id, revision=row.revision, digest=row.digest, spec=row.spec
        )

    async def create(self, owner_id, request):
        payload = request.spec.model_dump(mode="json")
        digest = revision_digest(request.spec)
        async with self.sessions.begin() as session:
            await session.execute(
                insert(RegistryAsset)
                .values(id=request.asset_id, owner_id=owner_id, kind=request.spec.kind)
                .on_conflict_do_nothing(index_elements=[RegistryAsset.id])
            )
            asset = await session.scalar(
                select(RegistryAsset).where(RegistryAsset.id == request.asset_id).with_for_update()
            )
            if asset.owner_id != owner_id:
                raise ApiFailure(404, ErrorCode.NOT_FOUND, "资产不存在")
            if asset.kind != request.spec.kind:
                raise ApiFailure(409, ErrorCode.CONFLICT, "资产类型不能改变")
            existing = await session.get(RegistryRevision, (request.asset_id, request.revision))
            if existing is not None:
                if existing.spec != payload or existing.digest != digest:
                    raise ApiFailure(409, ErrorCode.CONFLICT, "已冻结版本不能改变")
                return self.view(existing)
            latest = await session.scalar(
                select(func.max(RegistryRevision.revision)).where(
                    RegistryRevision.asset_id == request.asset_id
                )
            )
            if request.revision != (latest or 0) + 1:
                raise ApiFailure(409, ErrorCode.CONFLICT, "新版本必须连续递增")
            row = RegistryRevision(
                asset_id=request.asset_id, revision=request.revision, digest=digest, spec=payload
            )
            session.add(row)
            await session.flush()
            return self.view(row)

    async def read(self, owner_id, asset_id, revision):
        async with self.sessions() as session:
            row = await session.scalar(
                select(RegistryRevision)
                .join(RegistryAsset)
                .where(
                    RegistryAsset.id == asset_id,
                    RegistryAsset.owner_id == owner_id,
                    RegistryRevision.revision == revision,
                )
            )
            return self.view(row) if row is not None else None

    async def resolve_selection(self, owner_id, selection: RegistrySelection):
        refs = {"task": selection.task, "agent": selection.agent, "model": selection.model}
        resolved = {}
        async with self.sessions() as session:
            for name, ref in refs.items():
                row = await session.scalar(
                    select(RegistryRevision)
                    .join(RegistryAsset)
                    .where(
                        RegistryAsset.id == ref.id,
                        RegistryAsset.owner_id == owner_id,
                        RegistryRevision.revision == ref.revision,
                    )
                )
                if row is None:
                    raise ApiFailure(404, ErrorCode.NOT_FOUND, "资产版本不存在")
                revision = self.view(row)
                if revision.digest != ref.digest or revision.digest != revision_digest(
                    revision.spec
                ):
                    raise ApiFailure(409, ErrorCode.CONFLICT, "资产版本摘要不匹配")
                resolved[name] = revision
        expected = {"task": TaskSpec, "agent": AgentSpec, "model": ModelSpec}
        if any(not isinstance(resolved[name].spec, spec) for name, spec in expected.items()):
            raise ApiFailure(409, ErrorCode.CONFLICT, "资产版本类型不匹配")
        return resolved

    async def list(self, owner_id, asset_id, limit, after):
        async with self.sessions() as session:
            asset = await session.scalar(
                select(RegistryAsset).where(
                    RegistryAsset.id == asset_id, RegistryAsset.owner_id == owner_id
                )
            )
            if asset is None:
                raise ApiFailure(404, ErrorCode.NOT_FOUND, "资产不存在")
            rows = list(
                await session.scalars(
                    select(RegistryRevision)
                    .where(RegistryRevision.asset_id == asset_id, RegistryRevision.revision > after)
                    .order_by(RegistryRevision.revision)
                    .limit(limit + 1)
                )
            )
            return RevisionList(
                items=tuple(self.view(row) for row in rows[:limit]),
                next_cursor=rows[limit - 1].revision if len(rows) > limit else None,
            )


@router.post(
    "/registry/revisions",
    response_model=AssetRevision,
    tags=["registry"],
    operation_id="create_revision",
)
async def create_revision(
    body: RevisionCreate, identity: User, _gate: WriteGate, store: Store, request: Request
):
    return await Registry(request.app.state.engine).create(identity.subject_id, body)


@router.post(
    "/jobs/from-revisions",
    response_model=JobView,
    tags=["jobs"],
    operation_id="create_job_from_revisions",
)
async def create_job_from_revisions(
    body: RegistryJobCreateRequest, identity: User, _gate: WriteGate, store: Store, request: Request
):
    registry = Registry(request.app.state.engine)
    resolved = await registry.resolve_selection(identity.subject_id, body.revisions)
    task, agent, model = (resolved[key].spec for key in ("task", "agent", "model"))
    approved = next(
        (
            item
            for item in request.app.state.approved_tasks.values()
            if item.snapshot.runtime is not None
            and item.snapshot.runtime.task_archive_key == task.archive_key
            and item.snapshot.runtime.task_archive_digest == task.archive_digest
            and item.snapshot.runtime.agent_name == agent.agent_name
            and item.snapshot.runtime.agent_version == agent.agent_version
            and item.snapshot.runtime.model_name == model.model_name
            and item.snapshot.runtime.protocol == model.protocol
            and item.snapshot.runtime.model_base_url == model.model_base_url
            and item.snapshot.runtime.credential_mode == model.credential_mode
        ),
        None,
    )
    if approved is None:
        raise ApiFailure(403, ErrorCode.FORBIDDEN, "所选资产未匹配部署者审核的运行配置")
    runtime = approved.snapshot.runtime
    selection = RegistrySelection(
        task=RevisionRef(
            id=resolved["task"].asset_id,
            revision=resolved["task"].revision,
            digest=resolved["task"].digest,
        ),
        agent=RevisionRef(
            id=resolved["agent"].asset_id,
            revision=resolved["agent"].revision,
            digest=resolved["agent"].digest,
        ),
        model=RevisionRef(
            id=resolved["model"].asset_id,
            revision=resolved["model"].revision,
            digest=resolved["model"].digest,
        ),
    )
    snapshot = ExecutionSnapshot(
        task=RevisionRef(
            id=selection.task.id,
            revision=selection.task.revision,
            digest=approved.snapshot.task.digest,
        ),
        agent=RevisionRef(
            id=selection.agent.id, revision=selection.agent.revision, digest=runtime.agent_digest
        ),
        model=RevisionRef(
            id=selection.model.id, revision=selection.model.revision, digest=runtime.model_digest
        ),
        timeout_sec=body.timeout_sec,
        max_executions=body.max_executions,
        runtime=runtime,
        registry=selection,
    )
    return await store.create_job(
        CreateJob(
            job_id=body.job_id,
            owner_id=identity.subject_id,
            snapshot=snapshot,
            trial_count=body.trial_count,
        )
    )


@router.get(
    "/registry/assets/{asset_id}/revisions/{revision}",
    response_model=AssetRevision,
    tags=["registry"],
    operation_id="get_revision",
)
async def get_revision(
    asset_id: UUID,
    revision: Annotated[int, Path(ge=1, le=1000000)],
    identity: User,
    store: Store,
    request: Request,
):
    value = await Registry(request.app.state.engine).read(identity.subject_id, asset_id, revision)
    if value is None:
        raise ApiFailure(404, ErrorCode.NOT_FOUND, "资产版本不存在")
    return value


@router.get(
    "/registry/assets/{asset_id}/revisions",
    response_model=RevisionList,
    tags=["registry"],
    operation_id="list_revisions",
)
async def list_revisions(
    asset_id: UUID,
    identity: User,
    store: Store,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    after: Annotated[int, Query(ge=0, le=1000000)] = 0,
):
    return await Registry(request.app.state.engine).list(
        identity.subject_id, asset_id, limit, after
    )
