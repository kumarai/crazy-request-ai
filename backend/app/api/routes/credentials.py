from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.db.repositories.credentials import CredentialsRepository

logger = logging.getLogger("[api]")

router = APIRouter()


def _require_admin(request: Request) -> None:
    if not getattr(request.state, "is_admin", False):
        raise HTTPException(status_code=403, detail="Admin API key required")


class CreateCredentialRequest(BaseModel):
    name: str
    credential_type: str = "token"  # "token" | "ssh"
    value: str  # plaintext — will be encrypted before storage
    description: str | None = None


class UpdateCredentialRequest(BaseModel):
    name: str | None = None
    credential_type: str | None = None
    value: str | None = None  # if provided, re-encrypted
    description: str | None = None


@router.get("/credentials")
async def list_credentials(request: Request):
    """List all credentials (metadata only — never returns values)."""
    repo = CredentialsRepository(request.app.state.session_factory)
    creds = await repo.list_all()
    for c in creds:
        c["id"] = str(c["id"])
    return creds


@router.post("/credentials", status_code=201)
async def create_credential(body: CreateCredentialRequest, request: Request):
    """Create a credential. The value is encrypted before storage."""
    _require_admin(request)
    repo = CredentialsRepository(request.app.state.session_factory)
    cred = await repo.create(
        name=body.name,
        credential_type=body.credential_type,
        value=body.value,
        description=body.description,
    )
    cred["id"] = str(cred["id"])
    return cred


@router.get("/credentials/{credential_id}")
async def get_credential(credential_id: UUID, request: Request):
    """Get credential metadata (never returns the decrypted value)."""
    repo = CredentialsRepository(request.app.state.session_factory)
    cred = await repo.get(credential_id)
    if not cred:
        raise HTTPException(status_code=404, detail="Credential not found")
    cred["id"] = str(cred["id"])
    return cred


@router.put("/credentials/{credential_id}")
async def update_credential(
    credential_id: UUID,
    body: UpdateCredentialRequest,
    request: Request,
):
    """Update a credential. If value is provided, it is re-encrypted."""
    _require_admin(request)
    repo = CredentialsRepository(request.app.state.session_factory)
    cred = await repo.update(
        credential_id,
        name=body.name,
        credential_type=body.credential_type,
        value=body.value,
        description=body.description,
    )
    if not cred:
        raise HTTPException(status_code=404, detail="Credential not found")
    cred["id"] = str(cred["id"])
    return cred


@router.delete("/credentials/{credential_id}", status_code=204)
async def delete_credential(credential_id: UUID, request: Request):
    """Delete a credential. Sources referencing it will have credential_id set to NULL."""
    _require_admin(request)
    repo = CredentialsRepository(request.app.state.session_factory)
    deleted = await repo.delete(credential_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Credential not found")
