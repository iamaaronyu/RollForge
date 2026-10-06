"""只读取数据库已接受的执行产物；浏览器不能指定存储对象键。"""

import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import Query, Request
from fastapi.responses import PlainTextResponse
from rollforge_harbor_adapter.trajectory import UnsupportedTrajectory, project_trajectory
from rollforge_object_store.bundle import BundlePublisher
from rollforge_object_store.s3 import ObjectStoreError, S3ObjectStore
from rollforge_schemas.api import ArtifactIndex, ErrorCode
from rollforge_schemas.trajectory import TrajectoryView

from rollforge_api.routes import ApiFailure, Store, User, router


def read_artifacts(settings, scope, result, path=None):
    try:
        objects = S3ObjectStore(
            settings.object_storage_endpoint,
            settings.object_storage_bucket,
            settings.object_storage_access_key,
            settings.object_storage_secret_key,
            allow_local_http=settings.object_storage_allow_local_http,
        )
    except ValueError:
        raise ObjectStoreError("对象存储配置无效") from None
    try:
        manifest = BundlePublisher(objects, sensitive_values=()).inspect(
            scope, result.manifest_digest
        )
        if path is None:
            return ArtifactIndex(files=manifest.files, result=result)
        file = next((file for file in manifest.files if file.path == path), None)
        if file is None:
            raise ApiFailure(404, ErrorCode.NOT_FOUND, "产物不存在")
        if file.size > 1024 * 1024:
            raise ApiFailure(413, ErrorCode.INVALID_REQUEST, "文本预览最多支持 1 MiB")
        data = objects.read_verified(scope.prefix + "/files/" + file.path, file.digest, file.size)
        try:
            value = data.decode("utf-8")
        except UnicodeDecodeError:
            raise ApiFailure(
                422, ErrorCode.INVALID_REQUEST, "该产物不支持 UTF-8 文本预览"
            ) from None
        if "\x00" in value:
            raise ApiFailure(422, ErrorCode.INVALID_REQUEST, "该产物不支持文本预览")
        return value
    finally:
        objects.close()


@router.get(
    "/jobs/{job_id}/executions/{execution_id}/artifacts",
    response_model=ArtifactIndex,
    tags=["artifacts"],
    operation_id="artifact_index",
)
async def artifact_index(
    job_id: UUID, execution_id: UUID, identity: User, store: Store, request: Request
):
    accepted = await store.accepted_execution(job_id, execution_id, identity.subject_id)
    if accepted is None:
        raise ApiFailure(404, ErrorCode.NOT_FOUND, "执行产物不存在")
    return await asyncio.to_thread(read_artifacts, request.app.state.settings, *accepted)


@router.get(
    "/jobs/{job_id}/executions/{execution_id}/artifact-text",
    response_class=PlainTextResponse,
    tags=["artifacts"],
    operation_id="artifact_text",
    responses={413: {"description": "文本超过预览上限"}},
)
async def artifact_text(
    job_id: UUID,
    execution_id: UUID,
    identity: User,
    store: Store,
    request: Request,
    path: Annotated[str, Query(min_length=1, max_length=768)],
):
    accepted = await store.accepted_execution(job_id, execution_id, identity.subject_id)
    if accepted is None:
        raise ApiFailure(404, ErrorCode.NOT_FOUND, "执行产物不存在")
    value = await asyncio.to_thread(read_artifacts, request.app.state.settings, *accepted, path)
    return PlainTextResponse(
        value,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
        },
    )


@router.get(
    "/jobs/{job_id}/executions/{execution_id}/trajectory",
    response_model=TrajectoryView,
    tags=["artifacts"],
    operation_id="trajectory_view",
)
async def trajectory_view(
    job_id: UUID, execution_id: UUID, identity: User, store: Store, request: Request
):
    accepted = await store.accepted_execution(job_id, execution_id, identity.subject_id)
    if accepted is None:
        raise ApiFailure(404, ErrorCode.NOT_FOUND, "执行产物不存在")
    value = await asyncio.to_thread(
        read_artifacts, request.app.state.settings, *accepted, "agent/trajectory.json"
    )
    try:
        return project_trajectory(value)
    except UnsupportedTrajectory:
        raise ApiFailure(
            422, ErrorCode.INVALID_REQUEST, "轨迹格式不受支持，请查看原始文件"
        ) from None
