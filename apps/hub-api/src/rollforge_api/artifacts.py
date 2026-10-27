"""只读取数据库已接受的执行产物；浏览器不能指定存储对象键。"""

import asyncio
from pathlib import PurePosixPath
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import Query, Request
from fastapi.responses import PlainTextResponse, Response
from rollforge_harbor_adapter.trajectory import UnsupportedTrajectory, project_trajectory
from rollforge_object_store.bundle import BundlePublisher
from rollforge_object_store.s3 import MAX_OBJECT_BYTES, ObjectStoreError, S3ObjectStore
from rollforge_schemas.api import ArtifactIndex, ErrorCode
from rollforge_schemas.trajectory import TrajectoryView

from rollforge_api.routes import ApiFailure, Store, User, router

MAX_MEDIA_PREVIEW_BYTES = 8 * 1024 * 1024
MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".mpga": "audio/mpeg",
    ".mp4": "audio/mp4",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".flac": "audio/flac",
    ".webm": "audio/webm",
    ".aif": "audio/aiff",
    ".aiff": "audio/aiff",
}


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


def read_artifact_media(settings, scope, result, path: str) -> tuple[bytes, str]:
    """Return only small, manifest-listed media after full digest verification."""
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
        file = next((item for item in manifest.files if item.path == path), None)
        if file is None:
            raise ApiFailure(404, ErrorCode.NOT_FOUND, "产物不存在")
        media_type = MEDIA_TYPES.get(PurePosixPath(file.path).suffix.lower())
        if media_type is None:
            raise ApiFailure(415, ErrorCode.INVALID_REQUEST, "该产物不是支持预览的图片或音频")
        if file.size > MAX_MEDIA_PREVIEW_BYTES:
            raise ApiFailure(413, ErrorCode.INVALID_REQUEST, "媒体预览最多支持 8 MiB")
        data = objects.read_verified(scope.prefix + "/files/" + file.path, file.digest, file.size)
        return data, media_type
    finally:
        objects.close()


def read_artifact_download(settings, scope, result, path: str) -> tuple[bytes, str]:
    """Return one bounded, manifest-listed file after full digest verification."""
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
        file = next((item for item in manifest.files if item.path == path), None)
        if file is None:
            raise ApiFailure(404, ErrorCode.NOT_FOUND, "产物不存在")
        if file.size > MAX_OBJECT_BYTES:
            raise ApiFailure(413, ErrorCode.INVALID_REQUEST, "产物超过 64 MiB 下载上限")
        data = objects.read_verified(scope.prefix + "/files/" + file.path, file.digest, file.size)
        filename = PurePosixPath(file.path).name
        disposition = "attachment; filename*=UTF-8''" + quote(filename, safe="")
        return data, disposition
    finally:
        objects.close()


def read_external_trajectory(settings, scope, result, path: str) -> TrajectoryView:
    """Read one Manifest-listed ATIF JSON file; never resolve a URL or host path."""
    if path == "agent/trajectory.json":
        raise ApiFailure(404, ErrorCode.NOT_FOUND, "外部子轨迹不存在")
    if PurePosixPath(path).suffix.lower() != ".json":
        raise ApiFailure(415, ErrorCode.INVALID_REQUEST, "外部子轨迹必须是 JSON 文件")
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
        file = next((item for item in manifest.files if item.path == path), None)
        if file is None:
            raise ApiFailure(404, ErrorCode.NOT_FOUND, "外部子轨迹不存在")
        if file.size > 1024 * 1024:
            raise ApiFailure(413, ErrorCode.INVALID_REQUEST, "外部轨迹最多支持 1 MiB")
        data = objects.read_verified(scope.prefix + "/files/" + file.path, file.digest, file.size)
        try:
            value = data.decode("utf-8")
        except UnicodeDecodeError:
            raise ApiFailure(422, ErrorCode.INVALID_REQUEST, "外部轨迹不是 UTF-8 JSON") from None
        if "\x00" in value:
            raise ApiFailure(422, ErrorCode.INVALID_REQUEST, "外部轨迹格式不受支持")
        return project_trajectory(
            value,
            available_media_paths={
                item.path
                for item in manifest.files
                if item.path not in {file.path, "agent/trajectory.json"}
            },
        )
    except UnsupportedTrajectory:
        raise
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
    "/jobs/{job_id}/executions/{execution_id}/artifact-media",
    tags=["artifacts"],
    operation_id="artifact_media",
    responses={413: {"description": "媒体超过预览上限"}, 415: {"description": "媒体类型不支持"}},
)
async def artifact_media(
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
    data, media_type = await asyncio.to_thread(
        read_artifact_media, request.app.state.settings, *accepted, path
    )
    return Response(
        data,
        media_type=media_type,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
            "Cross-Origin-Resource-Policy": "same-origin",
        },
    )


@router.get(
    "/jobs/{job_id}/executions/{execution_id}/artifact-download",
    tags=["artifacts"],
    operation_id="artifact_download",
    response_class=Response,
    responses={
        200: {
            "description": "经 Manifest 与摘要验证的文件",
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        },
        413: {"description": "产物超过下载上限"},
    },
)
async def artifact_download(
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
    data, disposition = await asyncio.to_thread(
        read_artifact_download, request.app.state.settings, *accepted, path
    )
    return Response(
        data,
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
            "Content-Disposition": disposition,
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
    index = await asyncio.to_thread(read_artifacts, request.app.state.settings, *accepted)
    value = await asyncio.to_thread(
        read_artifacts, request.app.state.settings, *accepted, "agent/trajectory.json"
    )
    try:
        return project_trajectory(
            value,
            available_media_paths={
                item.path for item in index.files if item.path != "agent/trajectory.json"
            },
        )
    except UnsupportedTrajectory:
        raise ApiFailure(
            422, ErrorCode.INVALID_REQUEST, "轨迹格式不受支持，请查看原始文件"
        ) from None


@router.get(
    "/jobs/{job_id}/executions/{execution_id}/trajectory/external",
    response_model=TrajectoryView,
    tags=["artifacts"],
    operation_id="external_trajectory_view",
)
async def external_trajectory_view(
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
    try:
        return await asyncio.to_thread(
            read_external_trajectory, request.app.state.settings, *accepted, path
        )
    except UnsupportedTrajectory:
        raise ApiFailure(422, ErrorCode.INVALID_REQUEST, "外部轨迹格式不受支持") from None
