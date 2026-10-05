from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from rollforge_common.settings import Settings
from rollforge_schemas.domain import HealthResponse
from sqlalchemy import text

from rollforge_api.db import create_engine


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = create_engine(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await engine.dispose()

    app = FastAPI(title="RollForge Hub API", version="0.1.0", lifespan=lifespan)

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
            "message": "Harbor/E2B compatibility spike is required before enabling execution.",
        }

    return app


app = create_app()
