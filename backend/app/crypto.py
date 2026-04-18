"""Fernet-based credential encryption.

The encryption key is read from settings.security_encryption_key.
All credential values are encrypted at rest in the DB and decrypted only
when needed at runtime. The plaintext never touches logs or API responses.
"""
from __future__ import annotations

import logging

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger("[crypto]")

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key = settings.security_encryption_key
        if not key:
            raise RuntimeError(
                "security.encryption_key is not set in config.toml. "
                "Generate one with: python -c "
                '"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            )
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


def encrypt(plaintext: str) -> str:
    """Encrypt a plaintext string. Returns base64-encoded ciphertext."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt a ciphertext string. Returns plaintext.

    Raises ValueError if the ciphertext is invalid or the key is wrong.
    """
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as e:
        raise ValueError("Failed to decrypt credential — wrong key or corrupt data") from e
