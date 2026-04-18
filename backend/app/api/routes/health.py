from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

logger = logging.getLogger("[api]")

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    db_ok = False
    redis_ok = False

    # Check DB via SQLAlchemy
    try:
        async with request.app.state.session_factory() as session:
            await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception as e:
        logger.error("Health check DB failed: %s", e)

    # Check Redis
    try:
        redis_client = request.app.state.redis
        await redis_client.ping()
        redis_ok = True
    except Exception as e:
        logger.error("Health check Redis failed: %s", e)

    status = "ok" if (db_ok and redis_ok) else "degraded"
    status_code = 200 if (db_ok and redis_ok) else 503

    return JSONResponse(
        status_code=status_code,
        content={
            "status": status,
            "db": "ok" if db_ok else "error",
            "redis": "ok" if redis_ok else "error",
            "version": "1.0.0",
        },
    )
