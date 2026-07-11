from __future__ import annotations

import hmac
import os
import threading

from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .database import Database


class VaultError(ValueError):
    pass


class Vault:
    def __init__(self, db: Database):
        self.db = db
        self._key: bytes | None = None
        self._lock = threading.RLock()

    @staticmethod
    def _derive(password: str, salt: bytes) -> bytes:
        if not password:
            raise VaultError("主密码不能为空")
        return hash_secret_raw(password.encode(), salt, 3, 65536, 2, 32, Type.ID)

    @property
    def initialized(self) -> bool:
        return self.db.fetchone("SELECT id FROM vault WHERE id=1") is not None

    @property
    def unlocked(self) -> bool:
        return self._key is not None

    def initialize(self, password: str) -> None:
        with self._lock:
            if self.initialized:
                raise VaultError("保险库已初始化")
            salt = os.urandom(16)
            key = self._derive(password, salt)
            verifier = hmac.digest(key, b"webssh-vault-verifier", "sha256")
            self.db.execute("INSERT INTO vault(id,salt,verifier) VALUES(1,?,?)", (salt, verifier))
            self._key = key

    def unlock(self, password: str) -> None:
        with self._lock:
            row = self.db.fetchone("SELECT salt,verifier FROM vault WHERE id=1")
            if not row:
                raise VaultError("保险库尚未初始化")
            key = self._derive(password, row["salt"])
            expected = hmac.digest(key, b"webssh-vault-verifier", "sha256")
            if not hmac.compare_digest(expected, row["verifier"]):
                raise VaultError("主密码错误")
            self._key = key

    def lock(self) -> None:
        self._key = None

    def encrypt(self, value: str | None) -> bytes | None:
        if not value:
            return None
        if self._key is None:
            raise VaultError("保险库已锁定")
        nonce = os.urandom(12)
        return nonce + AESGCM(self._key).encrypt(nonce, value.encode(), b"webssh-secret-v1")

    def decrypt(self, value: bytes | None) -> str | None:
        if value is None:
            return None
        if self._key is None:
            raise VaultError("保险库已锁定")
        try:
            return AESGCM(self._key).decrypt(value[:12], value[12:], b"webssh-secret-v1").decode()
        except Exception as exc:
            raise VaultError("凭据解密失败") from exc
