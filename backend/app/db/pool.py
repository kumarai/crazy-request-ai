from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

logger = logging.getLogger("[db]")


def create_engine(
    url: str,
    pool_size: int = 15,
    pool_min: int = 2,
    echo: bool = False,
) -> AsyncEngine:
    engine = create_async_engine(
        url,
        pool_size=pool_size,
        max_overflow=pool_size - pool_min,
        echo=echo,
        pool_pre_ping=True,
    )
    logger.info("SQLAlchemy async engine created (pool_size=%d)", pool_size)
    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
