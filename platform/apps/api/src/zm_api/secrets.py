"""Sealed client secrets (S1-04): clients.secrets is Fernet-encrypted JSON.

The key is SECRETS_ENCRYPTION_KEY (`Fernet.generate_key()`); losing it loses every client's secrets.
"""

import json
import os
from typing import Any

from cryptography.fernet import Fernet


def _fernet() -> Fernet:
    key = os.environ.get("SECRETS_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("set SECRETS_ENCRYPTION_KEY")
    return Fernet(key)


def seal(data: dict[str, Any]) -> bytes:
    return _fernet().encrypt(json.dumps(data).encode())


def unseal(token: bytes) -> dict[str, Any]:
    """Raises cryptography.fernet.InvalidToken for a wrong key or tampered bytes."""
    return json.loads(_fernet().decrypt(token))
