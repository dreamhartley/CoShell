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
from pathlib import Path
from typing import Any

import paramiko
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

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


def create_ssh_key_pair(key_type: str, rsa_bits: int, passphrase: str | None, comment: str = "") -> tuple[str, str]:
    """Create an OpenSSH private/public key pair without invoking ssh-keygen."""
    if key_type == "ed25519":
        private_key = ed25519.Ed25519PrivateKey.generate()
    elif key_type == "rsa" and rsa_bits in (2048, 3072, 4096):
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=rsa_bits)
    else:
        raise ValueError("不支持的 SSH 密钥类型或长度")
    encryption = serialization.BestAvailableEncryption(passphrase.encode("utf-8")) if passphrase else serialization.NoEncryption()
    private_text = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        encryption,
    ).decode("utf-8").rstrip("\n") + "\n"
    public_text = private_key.public_key().public_bytes(
        serialization.Encoding.OpenSSH,
        serialization.PublicFormat.OpenSSH,
    ).decode("ascii")
    if comment:
        public_text += " " + comment.replace("\r", " ").replace("\n", " ").strip()
    return private_text, public_text + "\n"


def save_ssh_key_pair(directory: Path, file_name: str, private_key: str, public_key: str) -> tuple[Path, Path]:
    """Save both files exclusively so an existing key is never overwritten."""
    directory.mkdir(parents=True, exist_ok=True)
    private_path = directory / file_name
    public_path = directory / f"{file_name}.pub"
    if private_path.exists() or public_path.exists():
        raise FileExistsError("同名密钥文件已存在")
    try:
        with private_path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(private_key)
        try:
            private_path.chmod(0o600)
        except OSError:
            pass
        with public_path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(public_key)
    except Exception:
        private_path.unlink(missing_ok=True)
        public_path.unlink(missing_ok=True)
        raise
    return private_path, public_path


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


OS_ALIASES = {
    "ubuntu": "ubuntu", "debian": "debian", "raspbian": "debian", "kali": "kali",
    "fedora": "fedora", "centos": "centos", "rhel": "rhel", "redhat": "rhel",
    "rocky": "rocky", "almalinux": "alma", "alma": "alma", "ol": "oracle",
    "arch": "arch", "manjaro": "arch", "endeavouros": "arch",
    "alpine": "alpine", "opensuse": "opensuse", "sles": "opensuse", "suse": "opensuse",
    "gentoo": "gentoo", "void": "void", "linuxmint": "mint", "mint": "mint",
    "amzn": "amazon", "amazon": "amazon", "freebsd": "freebsd", "darwin": "macos",
    "windows": "windows",
}


def normalize_remote_os(output: str) -> str:
    """Turn /etc/os-release (or uname fallback) into a stable icon identifier."""
    values: dict[str, str] = {}
    for raw_line in output[:32768].splitlines():
        if "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        values[key.strip().upper()] = value.strip().strip("'\"").lower()
    candidates = [values.get("ID", ""), *values.get("ID_LIKE", "").replace(",", " ").split()]
    for candidate in candidates:
        canonical = OS_ALIASES.get(candidate)
        if canonical:
            return canonical
    lowered = output.lower()
    for candidate, canonical in OS_ALIASES.items():
        if candidate in lowered:
            return canonical
    return "linux" if "linux" in lowered or values else "default"


def detect_remote_os(session: SSHSession) -> str:
    command = "LC_ALL=C sh -c 'if [ -r /etc/os-release ]; then cat /etc/os-release; else echo ID=$(uname -s 2>/dev/null); fi'"
    _, stdout, stderr = session.client.exec_command(command, timeout=3)
    output = stdout.read(32768).decode("utf-8", "replace")
    if not output:
        error = stderr.read(4096).decode("utf-8", "replace").strip()
        # Windows OpenSSH normally starts cmd.exe instead of a POSIX shell.
        _, windows_stdout, _ = session.client.exec_command("cmd /c ver", timeout=3)
        output = windows_stdout.read(4096).decode("utf-8", "replace")
        if not output and error:
            raise OSError(error)
    return normalize_remote_os(output)


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
