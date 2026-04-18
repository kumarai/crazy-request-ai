"""Lightweight session auth for the support chat.

This is a mock auth layer — the user's identity is ``customer_id`` and
nothing else. Login takes a customer_id, wraps it in an HMAC-signed
cookie, and issues it as ``support_session``. Every support turn
reads the cookie to resolve identity; missing / invalid cookie =
guest mode.

Guests get a stable ``guest-<uuid>`` id so the conversation thread
persists across the guest's session and can be rebound to a real
customer_id when they sign in.

The secret lives in config (``security_encryption_key`` is reused).
Cookie is ``HttpOnly`` + ``SameSite=Lax``; ``Secure`` should be set
when serving over HTTPS (currently only set when ``SESSION_COOKIE_SECURE``
env var is truthy).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass

from fastapi import Request, Response

from app.config import settings

logger = logging.getLogger("[support]")

COOKIE_NAME = "support_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 7  # 7 days

GUEST_PREFIX = "guest-"


@dataclass
class SessionIdentity:
    """Result of reading the request's session.

    ``customer_id`` is always non-empty. ``is_guest`` is True when the
    request came in with no cookie — we mint a guest id that the client
    stores for the rest of the browser session. ``issued_at`` is the
    cookie's issuance timestamp (seconds) and is used for expiry.
    """
    customer_id: str
    is_guest: bool
    issued_at: int


def _secret_bytes() -> bytes:
    # Reuse the existing Fernet secret; pad if shorter than 32 bytes.
    key = settings.security_encryption_key or os.environ.get(
        "SUPPORT_SESSION_SECRET", "dev-insecure-session-secret"
    )
    # HMAC works with any byte length; just utf-8 encode
    return key.encode("utf-8")


def _sign(payload: bytes) -> str:
    mac = hmac.new(_secret_bytes(), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")


def _encode_cookie(customer_id: str) -> str:
    body = {
        "cid": customer_id,
        "iat": int(time.time()),
    }
    raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
    b64 = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    sig = _sign(raw)
    return f"{b64}.{sig}"


def _decode_cookie(token: str) -> SessionIdentity | None:
    try:
        b64, sig = token.split(".", 1)
    except ValueError:
        return None
    padded = b64 + "=" * (-len(b64) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    except Exception:
        return None
    if not hmac.compare_digest(sig, _sign(raw)):
        return None
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return None
    cid = body.get("cid")
    iat = int(body.get("iat", 0))
    if not isinstance(cid, str) or not cid:
        return None
    if time.time() - iat > COOKIE_MAX_AGE:
        return None
    return SessionIdentity(
        customer_id=cid,
        is_guest=cid.startswith(GUEST_PREFIX),
        issued_at=iat,
    )


def read_session(request: Request) -> SessionIdentity | None:
    """Return a valid SessionIdentity or ``None`` if no session is set."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    return _decode_cookie(token)


def issue_session(response: Response, customer_id: str) -> None:
    """Set the signed session cookie on the response."""
    secure = os.environ.get("SESSION_COOKIE_SECURE", "").lower() in {"1", "true"}
    response.set_cookie(
        key=COOKIE_NAME,
        value=_encode_cookie(customer_id),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


def make_guest_id() -> str:
    """Stable id for a guest browser session."""
    return f"{GUEST_PREFIX}{uuid.uuid4().hex[:12]}"


def is_guest_id(customer_id: str) -> bool:
    return customer_id.startswith(GUEST_PREFIX)
