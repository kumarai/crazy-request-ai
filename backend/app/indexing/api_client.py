"""HTTP client for fetching data from remote APIs (REST, GraphQL, etc.).

Supports multiple auth strategies and pagination modes.  All config is
read from the ``Source.config`` JSONB column — nothing lives in files.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib.parse import urljoin

import httpx
import jmespath

logger = logging.getLogger("[api-client]")

_DEFAULT_TIMEOUT = 30.0
_MAX_RESPONSE_BYTES = 50 * 1024 * 1024  # 50 MB safety cap
_RETRY_AFTER_DEFAULT = 5  # seconds


class ApiClient:
    """Fetches paginated data from a remote API using config-driven settings."""

    def __init__(
        self,
        config: dict[str, Any],
        credential_value: str | None = None,
        credential_type: str = "token",
    ) -> None:
        self._url: str = config["url"]
        self._method: str = config.get("method", "GET").upper()
        self._static_headers: dict = dict(config.get("headers") or {})
        self._query_params: dict = dict(config.get("query_params") or {})
        self._body: Any = config.get("body")
        self._auth_strategy: str = config.get("auth_strategy", "none")
        self._auth_header_name: str = config.get("auth_header_name", "Authorization")
        self._auth_query_param: str = config.get("auth_query_param_name", "api_key")
        self._pagination: dict = config.get("pagination") or {"type": "none"}
        self._response_format: str = config.get("response_format", "json")
        self._credential_value = credential_value
        self._credential_type = credential_type

        # OAuth2 token (fetched lazily)
        self._oauth2_token: str | None = None
        self._oauth2_expires_at: float = 0

    # ── public API ───────────────────────────────────────────────────

    async def fetch_all(self) -> list[Any]:
        """Fetch all pages and return a list of raw response payloads."""
        headers = await self._build_headers()
        params = dict(self._query_params)
        body = self._prepare_body()

        pag_type = self._pagination.get("type", "none")
        max_pages = int(self._pagination.get("max_pages", 50))

        all_payloads: list[Any] = []
        total_bytes = 0
        page = 0

        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            while page < max_pages:
                page += 1

                resp = await self._send_request(
                    client, headers, params, body
                )

                raw = resp.text
                total_bytes += len(raw.encode())
                if total_bytes > _MAX_RESPONSE_BYTES:
                    logger.warning(
                        "Response size cap reached (%d bytes) — stopping pagination",
                        total_bytes,
                    )
                    break

                payload = self._parse_response(raw)
                all_payloads.append(payload)

                # Determine next page
                if pag_type == "none":
                    break

                if pag_type == "cursor":
                    next_cursor = self._extract_cursor(payload)
                    if not next_cursor:
                        break
                    self._apply_cursor(params, body, next_cursor)

                elif pag_type == "offset":
                    limit = int(self._pagination.get("limit", 100))
                    offset_param = self._pagination.get("offset_param", "offset")
                    limit_param = self._pagination.get("limit_param", "limit")
                    params[limit_param] = str(limit)
                    current_offset = int(params.get(offset_param, 0))
                    params[offset_param] = str(current_offset + limit)
                    # Stop if we got fewer items than the limit
                    items = self._extract_items(payload)
                    if len(items) < limit:
                        break

                elif pag_type == "link_header":
                    next_url = self._parse_link_header(resp)
                    if not next_url:
                        break
                    self._url = next_url
                    params = {}  # URL already contains params

                else:
                    break

        logger.info(
            "Fetched %d pages (%d bytes) from %s",
            page, total_bytes, self._url,
        )
        return all_payloads

    # ── auth ─────────────────────────────────────────────────────────

    async def _build_headers(self) -> dict[str, str]:
        headers = dict(self._static_headers)

        if self._auth_strategy == "none":
            return headers

        if self._auth_strategy == "bearer":
            headers["Authorization"] = f"Bearer {self._credential_value}"

        elif self._auth_strategy == "header":
            headers[self._auth_header_name] = self._credential_value or ""

        elif self._auth_strategy == "oauth2":
            token = await self._get_oauth2_token()
            headers["Authorization"] = f"Bearer {token}"

        # query_param auth is handled in _send_request
        return headers

    async def _get_oauth2_token(self) -> str:
        """Perform client_credentials grant; cache until expiry."""
        if self._oauth2_token and time.time() < self._oauth2_expires_at:
            return self._oauth2_token

        cred = json.loads(self._credential_value or "{}")
        token_url = cred["token_url"]
        client_id = cred["client_id"]
        client_secret = cred["client_secret"]
        scope = cred.get("scope", "")

        data: dict[str, str] = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
        if scope:
            data["scope"] = scope

        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            token_data = resp.json()

        self._oauth2_token = token_data["access_token"]
        expires_in = int(token_data.get("expires_in", 3600))
        self._oauth2_expires_at = time.time() + expires_in - 60  # refresh 60s early

        logger.info("OAuth2 token acquired (expires_in=%ds)", expires_in)
        return self._oauth2_token

    # ── request ──────────────────────────────────────────────────────

    async def _send_request(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        params: dict,
        body: Any,
    ) -> httpx.Response:
        req_params = dict(params)

        if self._auth_strategy == "query_param":
            req_params[self._auth_query_param] = self._credential_value or ""

        kwargs: dict[str, Any] = {
            "method": self._method,
            "url": self._url,
            "headers": headers,
            "params": req_params or None,
        }
        if body is not None and self._method in ("POST", "PUT", "PATCH"):
            kwargs["json"] = body

        resp = await client.request(**kwargs)

        # Handle rate limiting with Retry-After
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", _RETRY_AFTER_DEFAULT))
            logger.warning("Rate limited — waiting %ds", retry_after)
            import asyncio
            await asyncio.sleep(retry_after)
            resp = await client.request(**kwargs)

        # Handle 401 for oauth2 — refresh token and retry once
        if resp.status_code == 401 and self._auth_strategy == "oauth2":
            logger.info("Got 401 — refreshing OAuth2 token")
            self._oauth2_token = None
            token = await self._get_oauth2_token()
            headers["Authorization"] = f"Bearer {token}"
            kwargs["headers"] = headers
            resp = await client.request(**kwargs)

        resp.raise_for_status()
        return resp

    # ── response parsing ─────────────────────────────────────────────

    def _parse_response(self, raw: str) -> Any:
        if self._response_format == "json":
            return json.loads(raw)
        if self._response_format == "xml":
            return raw  # XML parsing handled by ApiResponseParser
        return raw  # text

    def _prepare_body(self) -> Any:
        if self._body is None:
            return None
        if isinstance(self._body, str):
            try:
                return json.loads(self._body)
            except json.JSONDecodeError:
                return self._body
        return self._body  # already a dict/list

    # ── pagination helpers ───────────────────────────────────────────

    def _extract_cursor(self, payload: Any) -> str | None:
        cursor_path = self._pagination.get("cursor_path", "")
        if not cursor_path or not isinstance(payload, dict):
            return None
        result = jmespath.search(cursor_path, payload)
        return str(result) if result else None

    def _apply_cursor(
        self, params: dict, body: Any, cursor: str
    ) -> None:
        cursor_param = self._pagination.get("cursor_param", "cursor")

        # GraphQL: inject cursor into request body variables
        if cursor_param.startswith("variables.") and isinstance(body, dict):
            var_key = cursor_param.split(".", 1)[1]
            if "variables" not in body:
                body["variables"] = {}
            body["variables"][var_key] = cursor
        else:
            params[cursor_param] = cursor

    def _extract_items(self, payload: Any) -> list:
        """Extract items using data_path for offset pagination item counting."""
        data_path = self._pagination.get("data_path") or ""
        if data_path and isinstance(payload, dict):
            result = jmespath.search(data_path, payload)
            if isinstance(result, list):
                return result
        if isinstance(payload, list):
            return payload
        return []

    @staticmethod
    def _parse_link_header(resp: httpx.Response) -> str | None:
        link = resp.headers.get("Link", "")
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split(";")[0].strip().strip("<>")
                return url
        return None
