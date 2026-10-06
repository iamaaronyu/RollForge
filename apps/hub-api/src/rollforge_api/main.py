from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from rollforge_common.settings import Settings
from rollforge_schemas.api import ApiError, ErrorCode
from rollforge_schemas.domain import HealthResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from rollforge_api.auth import Authenticator
from rollforge_api.db import create_engine
from rollforge_api.execution_service import LeaseRejected, SubmissionConflict
from rollforge_api.routes import ApiFailure, router


def create_app(settings: Settings | None = None, *, engine: AsyncEngine | None = None) -> FastAPI:
    settings = settings or Settings()
    auth = Authenticator(settings.api_credentials.get_secret_value())
    if settings.control_plane_writes_enabled and not auth.credentials:
        raise ValueError("启用控制面写接口必须配置鉴权")
    owns_engine = engine is None
    engine = engine if engine is not None else create_engine(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        if owns_engine:
            await engine.dispose()

    app = FastAPI(title="RollForge Hub API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.auth = auth
    app.include_router(router)

    def error(status: int, code: ErrorCode, message: str):
        return JSONResponse(
            status_code=status,
            content=ApiError(
                code=code,
                message=message,
            ).model_dump(mode="json"),
            headers={"WWW-Authenticate": "Bearer"} if status == 401 else None,
        )

    @app.exception_handler(ApiFailure)
    async def api_failure(_request, exc: ApiFailure):
        return error(exc.status, exc.error.code, exc.error.message)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _exc):
        # 默认错误会回显非法字段和输入；请求可能意外包含密钥，禁止回显。
        return error(422, ErrorCode.INVALID_REQUEST, "请求字段或格式无效")

    @app.exception_handler(LeaseRejected)
    async def rejected_lease(_request, _exc):
        return error(409, ErrorCode.LEASE_REJECTED, "租约无效、过期或已被替换")

    @app.exception_handler(SubmissionConflict)
    async def conflict(_request, _exc):
        return error(409, ErrorCode.CONFLICT, "请求与已保存数据冲突")

    @app.exception_handler(SQLAlchemyError)
    @app.exception_handler(ConnectionError)
    @app.exception_handler(TimeoutError)
    async def database_unavailable(_request, _exc):
        return error(503, ErrorCode.DATABASE_UNAVAILABLE, "数据库操作暂不可用")

    @app.get("/health/live", response_model=HealthResponse, tags=["health"])
    async def live():
        return HealthResponse(status="ok", service="hub-api", version="0.1.0")

    @app.get("/health/ready", tags=["health"])
    async def ready():
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception:
            # Never expose connection strings or raw infrastructure errors.
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        return {"status": "ready", "dependencies": {"postgres": "ok"}}

    @app.get("/api/v1/platform", tags=["platform"])
    async def platform():
        return {
            "name": "RollForge",
            "version": "0.1.0",
            "stage": "runtime-spike",
            "execution_enabled": False,
            "control_plane_writes_enabled": settings.control_plane_writes_enabled,
            "message": "Harbor/E2B compatibility spike is required before enabling execution.",
        }

    return app


app = create_app()
