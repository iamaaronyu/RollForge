import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from rollforge_object_store.bundle import BundlePublisher
from rollforge_object_store.s3 import ObjectStoreError, S3ObjectStore
from rollforge_schemas.api import (
    ApiError,
    ApprovedJobCreateRequest,
    ApprovedTask,
    ApprovedTaskList,
    ClaimRequest,
    ErrorCode,
    FinishRequest,
    JobCreateRequest,
    LeaseReference,
    Principal,
    RenewRequest,
    Role,
)
from rollforge_schemas.execution import (
    CreateJob,
    ExecutionList,
    JobList,
    JobView,
    Lease,
    LeaseIdentity,
    TrialView,
)
from rollforge_schemas.storage import ExecutionScope

from rollforge_api.execution_service import ExecutionService

bearer = HTTPBearer(auto_error=False)
router = APIRouter(
    prefix="/api/v1",
    responses={code: {"model": ApiError} for code in (401, 403, 404, 409, 422, 503)},
)


class ApiFailure(Exception):
    def __init__(self, status: int, code: ErrorCode, message: str):
        self.status = status
        self.error = ApiError(code=code, message=message)


def principal(
    request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
):
    identity = request.app.state.auth.authenticate(credentials.credentials) if credentials else None
    if identity is None:
        raise ApiFailure(401, ErrorCode.UNAUTHORIZED, "需要有效的访问凭证")
    return identity


def user(identity: Annotated[Principal, Depends(principal)]):
    if identity.role != Role.USER:
        raise ApiFailure(403, ErrorCode.FORBIDDEN, "需要用户权限")
    return identity


def worker(identity: Annotated[Principal, Depends(principal)]):
    if identity.role != Role.WORKER:
        raise ApiFailure(403, ErrorCode.FORBIDDEN, "需要 Worker 权限")
    return identity


def service(request: Request) -> ExecutionService:
    if request.app.state.engine.dialect.name != "postgresql":
        raise ApiFailure(503, ErrorCode.DATABASE_UNAVAILABLE, "控制面需要 PostgreSQL")
    return ExecutionService(request.app.state.engine)


def writes(request: Request):
    if not request.app.state.settings.control_plane_writes_enabled:
        raise ApiFailure(503, ErrorCode.WRITES_DISABLED, "控制面写接口尚未启用")


User = Annotated[Principal, Depends(user)]
Worker = Annotated[Principal, Depends(worker)]
Store = Annotated[ExecutionService, Depends(service)]
WriteGate = Annotated[None, Depends(writes)]


def owned(lease: LeaseReference, identity: Principal):
    return LeaseIdentity(**lease.model_dump(), worker_id=identity.subject_id)


@router.post("/jobs", response_model=JobView, tags=["jobs"], operation_id="create_job")
async def create_job(body: JobCreateRequest, identity: User, _gate: WriteGate, store: Store):
    return await store.create_job(
        CreateJob(
            job_id=body.job_id,
            owner_id=identity.subject_id,
            snapshot=body.snapshot,
        )
    )


@router.get(
    "/tasks/approved", response_model=ApprovedTaskList, tags=["jobs"], operation_id="approved_tasks"
)
async def approved_tasks(identity: User, request: Request):
    return ApprovedTaskList(
        items=tuple(
            ApprovedTask(id=item.id, label=item.label)
            for item in request.app.state.approved_tasks.values()
        )
    )


@router.post(
    "/jobs/from-approved-task",
    response_model=JobView,
    tags=["jobs"],
    operation_id="create_approved_job",
)
async def create_approved_job(
    body: ApprovedJobCreateRequest, identity: User, _gate: WriteGate, store: Store, request: Request
):
    item = request.app.state.approved_tasks.get(body.task_id)
    if item is None:
        raise ApiFailure(404, ErrorCode.NOT_FOUND, "任务未配置或未审核")
    return await store.create_job(
        CreateJob(job_id=body.job_id, owner_id=identity.subject_id, snapshot=item.snapshot)
    )


@router.get("/jobs", response_model=JobList, tags=["jobs"], operation_id="list_jobs")
async def list_jobs(
    identity: User,
    store: Store,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    after: UUID | None = None,
):
    return await store.list_jobs(identity.subject_id, limit, after)


@router.get(
    "/jobs/{job_id}/executions",
    response_model=ExecutionList,
    tags=["jobs"],
    operation_id="list_executions",
)
async def list_executions(
    job_id: UUID,
    identity: User,
    store: Store,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    after: Annotated[int, Query(ge=0)] = 0,
):
    result = await store.list_executions(job_id, identity.subject_id, limit, after)
    if result is None:
        raise ApiFailure(404, ErrorCode.NOT_FOUND, "Job 不存在")
    return result


@router.get("/jobs/{job_id}", response_model=JobView, tags=["jobs"], operation_id="get_job")
async def get_job(job_id: UUID, identity: User, store: Store):
    job = await store.get_job(job_id, identity.subject_id)
    if job is None:
        raise ApiFailure(404, ErrorCode.NOT_FOUND, "Job 不存在")
    return job


@router.post(
    "/worker/leases/claim",
    response_model=Lease,
    tags=["worker"],
    operation_id="claim_lease",
    responses={204: {"description": "没有可领取的 Trial"}},
)
async def claim(body: ClaimRequest, identity: Worker, _gate: WriteGate, store: Store):
    lease = await store.claim(identity.subject_id, body.lease_seconds, body.runnable_only)
    return lease if lease is not None else Response(status_code=204)


@router.post(
    "/worker/leases/renew", response_model=Lease, tags=["worker"], operation_id="renew_lease"
)
async def renew(body: RenewRequest, identity: Worker, _gate: WriteGate, store: Store):
    return await store.renew(owned(body.lease, identity), body.lease_seconds)


@router.post(
    "/worker/leases/finish", response_model=TrialView, tags=["worker"], operation_id="finish_lease"
)
async def finish(
    body: FinishRequest, identity: Worker, _gate: WriteGate, store: Store, request: Request
):
    async def verify(lease, result):
        settings = request.app.state.settings
        if (
            not settings.object_storage_access_key.get_secret_value()
            or not settings.object_storage_secret_key.get_secret_value()
        ):
            raise ObjectStoreError("对象存储尚未配置")
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
            scope = ExecutionScope(
                job_id=lease.job_id,
                trial_id=lease.trial_id,
                execution_id=lease.execution_id,
                fencing_token=lease.fencing_token,
            )
            verified = await asyncio.to_thread(
                BundlePublisher(objects, sensitive_values=()).verify, scope, result.manifest_digest
            )
            if verified != result:
                raise ObjectStoreError("提交结果与已上传 Manifest 不一致")
        finally:
            objects.close()

    return await store.finish(owned(body.lease, identity), body.result, verifier=verify)
