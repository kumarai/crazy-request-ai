from __future__ import annotations

from typing import AsyncGenerator

import redis.asyncio as aioredis
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.client import LLMClient


async def get_session_factory(request: Request):
    return request.app.state.session_factory


async def get_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    async with request.app.state.session_factory() as session:
        yield session


async def get_redis(request: Request) -> aioredis.Redis:
    return request.app.state.redis


def get_llm_client(request: Request) -> LLMClient:
    return request.app.state.llm_client
