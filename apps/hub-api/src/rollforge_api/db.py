from rollforge_common.settings import Settings
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def create_engine(settings: Settings):
    return create_async_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)
