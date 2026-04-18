"""Login / logout / whoami for the support chat.

POST /v1/login  { customer_id } -> 200 { customer_id, is_guest: false }
POST /v1/logout                  -> 204
GET  /v1/whoami                  -> { customer_id, is_guest }
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.support.auth import (
    clear_session,
    is_guest_id,
    issue_session,
    make_guest_id,
    read_session,
)

logger = logging.getLogger("[api]")

router = APIRouter()


class LoginRequest(BaseModel):
    customer_id: str = Field(min_length=1, max_length=128)


class WhoAmIResponse(BaseModel):
    customer_id: str
    is_guest: bool


@router.post("/login", response_model=WhoAmIResponse)
async def login(body: LoginRequest, response: Response) -> WhoAmIResponse:
    """Mock login — any non-guest customer_id is accepted.

    Real auth (password, SSO, OTP) goes here in production. Keeping it
    trivial now means the chat frontend can be built + demoed without
    wiring an identity provider.
    """
    cid = body.customer_id.strip()
    if is_guest_id(cid):
        raise HTTPException(
            status_code=400,
            detail="cannot_login_with_guest_id",
        )
    issue_session(response, cid)
    return WhoAmIResponse(customer_id=cid, is_guest=False)


@router.post("/logout", status_code=204)
async def logout(response: Response) -> Response:
    clear_session(response)
    return Response(status_code=204)


@router.get("/whoami", response_model=WhoAmIResponse)
async def whoami(request: Request, response: Response) -> WhoAmIResponse:
    """Return the current session identity.

    Mints a new guest id on first visit so the client has a stable id
    to send on subsequent support turns. Authenticated customers see
    ``is_guest=false``.
    """
    ident = read_session(request)
    if ident is None:
        gid = make_guest_id()
        issue_session(response, gid)
        return WhoAmIResponse(customer_id=gid, is_guest=True)
    return WhoAmIResponse(
        customer_id=ident.customer_id, is_guest=ident.is_guest
    )
