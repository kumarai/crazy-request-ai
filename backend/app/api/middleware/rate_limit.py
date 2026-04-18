from __future__ import annotations

import logging
import time

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from app.config import settings

logger = logging.getLogger("[api]")

_WINDOW_SECONDS = 60


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        api_key = request.headers.get("X-API-Key", "")
        if not api_key:
            return await call_next(request)

        redis_client = getattr(request.app.state, "redis", None)
        if not redis_client:
            return await call_next(request)

        rate_key = f"rate:{api_key[-8:]}"
        now = time.time()
        window_start = now - _WINDOW_SECONDS

        try:
            pipe = redis_client.pipeline()
            # Remove expired entries
            pipe.zremrangebyscore(rate_key, 0, window_start)
            # Add current request
            pipe.zadd(rate_key, {str(now): now})
            # Count requests in window
            pipe.zcard(rate_key)
            # Set expiry on the key
            pipe.expire(rate_key, _WINDOW_SECONDS + 1)
            results = await pipe.execute()

            request_count = results[2]

            if request_count > settings.security_rate_limit_per_minute:
                retry_after = int(_WINDOW_SECONDS - (now - window_start))
                logger.warning(
                    "Rate limit exceeded for key ...%s (%d/%d)",
                    api_key[-4:],
                    request_count,
                    settings.security_rate_limit_per_minute,
                )
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": "rate_limit_exceeded",
                        "retry_after": max(retry_after, 1),
                    },
                    headers={"Retry-After": str(max(retry_after, 1))},
                )
        except Exception as e:
            # If Redis is down, allow the request through
            logger.error("Rate limit check failed: %s", e)

        return await call_next(request)
