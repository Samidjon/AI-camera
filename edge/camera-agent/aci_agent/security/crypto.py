"""Encryption for camera connection secrets (Fernet: AES-128-CBC + HMAC-SHA256).

The key is either provided via ACI_AGENT_SECRET_KEY or generated once into
``<data_dir>/secret.key`` with owner-only permissions. Losing the key makes stored
camera passwords unreadable (cameras must be re-entered) — back it up with the data dir.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class SecretDecryptionError(Exception):
    """Stored secret can't be decrypted (wrong or rotated key)."""


class SecretBox:
    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)

    @classmethod
    def from_settings(cls, explicit_key: str, key_path: Path) -> SecretBox:
        if explicit_key:
            return cls(explicit_key.encode("ascii"))
        return cls(load_or_create_key(key_path))

    def encrypt_json(self, data: dict) -> bytes:
        return self._fernet.encrypt(json.dumps(data, separators=(",", ":")).encode("utf-8"))

    def decrypt_json(self, token: bytes) -> dict:
        try:
            return json.loads(self._fernet.decrypt(token).decode("utf-8"))
        except (InvalidToken, ValueError) as exc:
            raise SecretDecryptionError("Camera secret can't be decrypted with the current key") from exc


def load_or_create_key(path: Path) -> bytes:
    if path.exists():
        return path.read_bytes().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    # O_EXCL: never overwrite a key another process just created.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    try:
        os.chmod(path, 0o600)  # effective on POSIX; Windows relies on the user profile ACL
    except OSError:
        pass
    return key
