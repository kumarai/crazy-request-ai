from __future__ import annotations

import json
import logging
import time

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

logger = logging.getLogger("[api]")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        start = time.time()

        response = await call_next(request)

        latency_ms = int((time.time() - start) * 1000)
        api_key_suffix = getattr(request.state, "api_key_suffix", "none")

        log_data = {
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "latency_ms": latency_ms,
            "api_key_suffix": api_key_suffix,
        }

        logger.info(json.dumps(log_data))
        return response
