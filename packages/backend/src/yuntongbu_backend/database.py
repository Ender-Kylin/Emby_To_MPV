from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import Settings


class Base(DeclarativeBase):
    pass


@dataclass(slots=True)
class DatabaseContext:
    engine: AsyncEngine
    session_maker: async_sessionmaker[AsyncSession]


def build_database(settings: Settings) -> DatabaseContext:
    settings.ensure_data_dir()
    engine = create_async_engine(settings.database_url, future=True)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    return DatabaseContext(engine=engine, session_maker=session_maker)


async def init_models(engine: AsyncEngine) -> None:
    from . import models  # noqa: F401

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
