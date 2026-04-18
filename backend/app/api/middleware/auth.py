from __future__ import annotations

import logging

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from app.config import settings

logger = logging.getLogger("[api]")

_SKIP_PATHS = {
    "/v1/health",
    "/docs",
    "/openapi.json",
    "/redoc",
    # Customer-support chat uses its own signed-cookie session. The
    # auth + query + action endpoints must be reachable without an
    # X-API-Key — that header is for admin / platform callers only.
    "/v1/login",
    "/v1/logout",
    "/v1/whoami",
    "/v1/support/query",
    "/v1/support/action",
}


# Prefix-matched skip list for paths with variable segments.
_SKIP_PREFIXES = (
    "/v1/support/conversations",  # list/read/totals/action_click
)

# All valid API keys (admin keys are a superset that also grants query access)
_ALL_KEYS: set[str] = set()
_ADMIN_KEYS: set[str] = set()


def _refresh_keys() -> None:
    global _ALL_KEYS, _ADMIN_KEYS
    _ADMIN_KEYS = set(settings.security_admin_api_keys)
    _ALL_KEYS = set(settings.security_api_keys) | _ADMIN_KEYS


class APIKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path
        if (
            path in _SKIP_PATHS
            or path.startswith(_SKIP_PREFIXES)
            or request.method == "OPTIONS"
        ):
            return await call_next(request)

        # Lazy-load keys on first request
        if not _ALL_KEYS:
            _refresh_keys()

        api_key = request.headers.get("X-API-Key")

        if not api_key or api_key not in _ALL_KEYS:
            logger.warning(
                "Unauthorized request to %s (key_suffix=%s)",
                request.url.path,
                api_key[-4:] if api_key else "none",
            )
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized", "code": "invalid_api_key"},
            )

        # Store role for downstream route guards
        request.state.api_key_suffix = api_key[-4:]
        request.state.is_admin = api_key in _ADMIN_KEYS
        return await call_next(request)
