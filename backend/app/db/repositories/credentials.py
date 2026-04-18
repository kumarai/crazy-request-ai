from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crypto import decrypt, encrypt
from app.db.models import Credential

logger = logging.getLogger("[db]")


class CredentialsRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create(
        self,
        name: str,
        credential_type: str,
        value: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Create a new credential. The value is encrypted before storage."""
        encrypted = encrypt(value)
        async with self._sf() as session:
            cred = Credential(
                name=name,
                credential_type=credential_type,
                encrypted_value=encrypted,
                description=description,
            )
            session.add(cred)
            await session.commit()
            await session.refresh(cred)
            return cred.to_dict()

    async def get(self, credential_id: UUID) -> dict[str, Any] | None:
        """Get credential metadata (no decrypted value)."""
        async with self._sf() as session:
            cred = await session.get(Credential, credential_id)
            if not cred:
                return None
            return cred.to_dict()

    async def get_decrypted_value(self, credential_id: UUID) -> str | None:
        """Decrypt and return the credential value. Never log the result."""
        async with self._sf() as session:
            cred = await session.get(Credential, credential_id)
            if not cred:
                return None
            return decrypt(cred.encrypted_value)

    async def list_all(self) -> list[dict[str, Any]]:
        """List all credentials (metadata only, no values)."""
        async with self._sf() as session:
            stmt = select(Credential).order_by(Credential.created_at)
            result = await session.execute(stmt)
            return [row.to_dict() for row in result.scalars().all()]

    async def update(
        self,
        credential_id: UUID,
        name: str | None = None,
        credential_type: str | None = None,
        value: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any] | None:
        """Update a credential. If value is provided, re-encrypt it."""
        async with self._sf() as session:
            cred = await session.get(Credential, credential_id)
            if not cred:
                return None
            if name is not None:
                cred.name = name
            if credential_type is not None:
                cred.credential_type = credential_type
            if value is not None:
                cred.encrypted_value = encrypt(value)
            if description is not None:
                cred.description = description
            await session.commit()
            await session.refresh(cred)
            return cred.to_dict()

    async def delete(self, credential_id: UUID) -> bool:
        async with self._sf() as session:
            stmt = delete(Credential).where(Credential.id == credential_id)
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    async def resolve_for_source(self, source: dict) -> str | None:
        """Resolve credential for a source dict. Returns decrypted value or None.

        Checks source.credential_id first, then falls back to
        config.credential_id if present.
        Never log the returned value.
        """
        cred_id = source.get("credential_id")
        if not cred_id:
            config = source.get("config", {})
            cred_id = config.get("credential_id")
        if not cred_id:
            return None

        uid = UUID(str(cred_id)) if not isinstance(cred_id, UUID) else cred_id
        return await self.get_decrypted_value(uid)
