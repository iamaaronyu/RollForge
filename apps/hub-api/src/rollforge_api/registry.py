"""PostgreSQL 版本冻结；注册不会授予执行许可。"""

from typing import Annotated
from uuid import UUID

from fastapi import Path, Query, Request
from rollforge_schemas.api import ErrorCode
from rollforge_schemas.registry import AssetRevision, RevisionCreate, RevisionList, revision_digest
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
