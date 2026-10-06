import asyncio

from alembic import context
from rollforge_api import models  # noqa: F401
from rollforge_api.db import Base, create_engine
from rollforge_common.settings import Settings


def configure(connection):
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


async def online():
    engine = create_engine(Settings())
    async with engine.connect() as connection:
        await connection.run_sync(configure)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(
        url=Settings().database_url.get_secret_value(),
        target_metadata=Base.metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()
elif context.config.attributes.get("connection") is not None:
    configure(context.config.attributes["connection"])
else:
    asyncio.run(online())
