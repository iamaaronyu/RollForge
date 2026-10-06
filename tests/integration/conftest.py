"""真实数据库测试共享隔离环境。"""

import os
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest_asyncio.fixture
async def engine():
    url = os.environ.get("ROLLFORGE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("真实 PostgreSQL 未配置：设置 ROLLFORGE_TEST_DATABASE_URL")
    if not url.startswith("postgresql+asyncpg://"):
        pytest.fail("验收必须使用 PostgreSQL + asyncpg")
    schema = "test_execution_" + uuid4().hex
    admin = create_async_engine(url)
    isolated = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    created = False

    def migrate(connection):
        root = Path(__file__).resolve().parents[2]
        config = Config(str(root / "apps/hub-api/alembic.ini"))
        config.attributes["connection"] = connection
        command.upgrade(config, "head")

    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            created = True
        async with isolated.begin() as connection:
            await connection.run_sync(migrate)
        yield isolated
    finally:
        await isolated.dispose()
        if created:
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()
