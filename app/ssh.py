from __future__ import annotations

import base64
import hashlib
import io
import posixpath
import secrets
import socket
import stat
import threading
import time
from dataclasses import dataclass
from typing import Any

import paramiko

from .database import Database


class HostKeyRequired(Exception):
    def __init__(self, host: str, port: int, key: paramiko.PKey, changed: bool = False):
        self.host, self.port, self.key, self.changed = host, port, key, changed
        digest = hashlib.sha256(key.asbytes()).digest()
        self.fingerprint = "SHA256:" + base64.b64encode(digest).decode().rstrip("=")
        super().__init__(self.fingerprint)


class VerifiedHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, db: Database, host: str, port: int, trusted_key: paramiko.PKey | None = None):
        self.db, self.host, self.port, self.trusted_key = db, host, port, trusted_key

    def missing_host_key(self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey) -> None:
        # A key accepted during this connection attempt is passed back in on the
        # retry. This avoids depending on a second database lookup in the narrow
        # window between the confirmation and Paramiko's new handshake.
        if self.trusted_key is not None:
            if not secrets.compare_digest(self.trusted_key.asbytes(), key.asbytes()):
                raise HostKeyRequired(self.host, self.port, key, changed=True)
            client.get_host_keys().add(hostname, key.get_name(), key)
            return
        row = self.db.fetchone("SELECT key_base64 FROM host_keys WHERE host=? AND port=?", (self.host, self.port))
        encoded = key.get_base64()
        if not row:
            raise HostKeyRequired(self.host, self.port, key)
        if not secrets.compare_digest(row["key_base64"], encoded):
            raise HostKeyRequired(self.host, self.port, key, changed=True)
        client.get_host_keys().add(hostname, key.get_name(), key)


def parse_private_key(value: str, passphrase: str | None) -> paramiko.PKey:
    errors: list[str] = []
    for cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey, paramiko.DSSKey):
        try:
            return cls.from_private_key(io.StringIO(value), password=passphrase)
        except Exception as exc:
            errors.append(str(exc))
    raise ValueError("无法解析私钥，请检查格式或口令")


def clean_remote_path(path: str) -> str:
    if not path or "\x00" in path:
        raise ValueError("无效的远程路径")
    normalized = posixpath.normpath(path.replace("\\", "/"))
    return normalized if normalized.startswith("/") else normalized


@dataclass
class SSHSession:
    id: str
    client: paramiko.SSHClient
    channel: paramiko.Channel
    sftp: paramiko.SFTPClient
    host: str
    port: int
    username: str
    server_id: int | None = None

    def close(self) -> None:
        for item in (self.sftp, self.channel, self.client):
            try:
                item.close()
            except Exception:
                pass


class SessionRegistry:
    def __init__(self, db: Database):
        self.db = db
        self._items: dict[str, SSHSession] = {}
        self._lock = threading.RLock()

    def connect(self, data: dict[str, Any]) -> SSHSession:
        host = str(data["host"]).strip()
        port = int(data.get("port", 22))
        username = str(data["username"]).strip()
        if not host or not username or not 1 <= port <= 65535:
            raise ValueError("连接参数无效")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(VerifiedHostKeyPolicy(self.db, host, port, data.get("trusted_host_key")))
        kwargs: dict[str, Any] = dict(
            hostname=host, port=port, username=username, timeout=12,
            banner_timeout=12, auth_timeout=15, allow_agent=False, look_for_keys=False,
        )
        if data.get("private_key"):
            kwargs["pkey"] = parse_private_key(data["private_key"], data.get("passphrase"))
        else:
            kwargs["password"] = data.get("password")
        try:
            client.connect(**kwargs)
            transport = client.get_transport()
            if not transport:
                raise ConnectionError("SSH 传输未建立")
            transport.set_keepalive(30)
            channel = client.invoke_shell(term="xterm-256color", width=int(data.get("cols", 100)), height=int(data.get("rows", 30)))
            sftp = client.open_sftp()
            server_id = int(data["server_id"]) if data.get("server_id") is not None else None
            session = SSHSession(secrets.token_urlsafe(24), client, channel, sftp, host, port, username, server_id)
            with self._lock:
                self._items[session.id] = session
            return session
        except Exception:
            client.close()
            raise

    def get(self, session_id: str) -> SSHSession:
        with self._lock:
            item = self._items.get(session_id)
        if not item:
            raise KeyError("会话不存在或已断开")
        transport = item.client.get_transport()
        if not transport or not transport.is_active():
            self.close(session_id)
            raise KeyError("会话不存在或已断开")
        return item

    def close(self, session_id: str) -> None:
        with self._lock:
            item = self._items.pop(session_id, None)
        if item:
            item.close()

    def close_all(self) -> None:
        with self._lock:
            items, self._items = list(self._items.values()), {}
        for item in items:
            item.close()


@dataclass
class UploadSession:
    id: str
    ssh_session_id: str
    path: str
    expected_size: int
    sftp: paramiko.SFTPClient
    remote: paramiko.SFTPFile
    written: int = 0
    updated_at: float = 0


class UploadRegistry:
    """Tracks direct-to-SFTP chunk uploads and their acknowledged remote progress."""

    def __init__(self):
        self._items: dict[str, UploadSession] = {}
        self._lock = threading.RLock()

    def create(self, ssh_session_id: str, sftp: paramiko.SFTPClient, path: str, size: int, overwrite: bool) -> UploadSession:
        if not overwrite:
            try:
                sftp.lstat(path)
                raise FileExistsError("目标文件已存在")
            except FileNotFoundError:
                pass
        remote = sftp.open(path, "wb")
        remote.set_pipelined(True)
        item = UploadSession(secrets.token_urlsafe(20), ssh_session_id, path, size, sftp, remote, updated_at=time.time())
        with self._lock:
            self._items[item.id] = item
        return item

    def write(self, upload_id: str, offset: int, chunk: bytes) -> UploadSession:
        with self._lock:
            item = self._items.get(upload_id)
            if not item:
                raise KeyError("上传任务不存在或已过期")
            if offset != item.written:
                raise ValueError(f"上传偏移不匹配，期望 {item.written}")
            item.remote.write(chunk)
            item.remote.flush()
            item.written += len(chunk)
            item.updated_at = time.time()
            return item

    def finish(self, upload_id: str) -> UploadSession:
        with self._lock:
            item = self._items.pop(upload_id, None)
        if not item:
            raise KeyError("上传任务不存在或已过期")
        try:
            item.remote.close()
        except Exception:
            self._remove_partial(item)
            raise
        if item.written != item.expected_size:
            self._remove_partial(item)
            raise ValueError("上传大小校验失败")
        return item

    def abort(self, upload_id: str, sftp: paramiko.SFTPClient | None = None) -> None:
        with self._lock:
            item = self._items.pop(upload_id, None)
        if item:
            try: item.remote.close()
            except Exception: pass
            remover = sftp or item.sftp
            if remover:
                try: remover.remove(item.path)
                except Exception: pass

    def close_for_session(self, ssh_session_id: str) -> None:
        with self._lock:
            ids = [key for key, item in self._items.items() if item.ssh_session_id == ssh_session_id]
        for upload_id in ids:
            self.abort(upload_id)

    @staticmethod
    def _remove_partial(item: UploadSession) -> None:
        try:
            item.remote.close()
        except Exception:
            pass
        try:
            item.sftp.remove(item.path)
        except Exception:
            pass


def file_info(attr: paramiko.SFTPAttributes) -> dict[str, Any]:
    return {
        "name": attr.filename,
        "size": attr.st_size,
        "mtime": attr.st_mtime,
        "mode": attr.st_mode,
        "is_dir": stat.S_ISDIR(attr.st_mode),
        "is_link": stat.S_ISLNK(attr.st_mode),
    }


def remove_recursive(sftp: paramiko.SFTPClient, path: str) -> None:
    info = sftp.lstat(path)
    if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
        for child in sftp.listdir_attr(path):
            remove_recursive(sftp, posixpath.join(path, child.filename))
        sftp.rmdir(path)
    else:
        sftp.remove(path)


def copy_recursive(sftp: paramiko.SFTPClient, source: str, destination: str) -> None:
    info = sftp.lstat(source)
    if stat.S_ISDIR(info.st_mode):
        sftp.mkdir(destination)
        for child in sftp.listdir_attr(source):
            copy_recursive(sftp, posixpath.join(source, child.filename), posixpath.join(destination, child.filename))
    else:
        with sftp.open(source, "rb") as src, sftp.open(destination, "wb") as dst:
            while chunk := src.read(1024 * 256):
                dst.write(chunk)
