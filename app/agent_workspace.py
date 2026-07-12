"""Sandboxed local workspace and SFTP transfers exposed to the SSH Agent."""

from __future__ import annotations

import hashlib
import os
import posixpath
import secrets
import shutil
import stat
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .ssh import SSHSession, clean_remote_path


MAX_WORKSPACE_TEXT = 256 * 1024
MAX_SFTP_TRANSFER = 512 * 1024 * 1024
MAX_LIST_ITEMS = 500
TRANSFER_CHUNK = 256 * 1024


class AgentWorkspace:
    def __init__(self, root: Path, session_getter: Callable[[str], SSHSession]):
        self.root = root.resolve()
        self.session_getter = session_getter
        self.root.mkdir(parents=True, exist_ok=True)

    def server_root(self, server_id: int) -> Path:
        return self.root / f"server-{int(server_id)}"

    def server_workspace_exists(self, server_id: int) -> bool:
        directory = self.server_root(server_id)
        return directory.is_dir() and next(directory.iterdir(), None) is not None

    def delete_server_workspace(self, server_id: int) -> bool:
        directory = self.server_root(server_id)
        if not directory.exists():
            return False
        try:
            directory.resolve().relative_to(self.root)
        except ValueError as exc:
            raise ValueError("服务器 workspace 路径无效") from exc
        if directory.is_symlink():
            directory.unlink()
        else:
            shutil.rmtree(directory)
        return True

    def _session_root(self, session_id: str) -> Path:
        session = self.session_getter(session_id)
        if session.server_id is not None:
            directory = self.server_root(session.server_id)
        else:
            identity = f"{session.username}@{session.host}:{session.port}".encode("utf-8")
            directory = self.root / f"connection-{hashlib.sha256(identity).hexdigest()[:16]}"
        directory.mkdir(parents=True, exist_ok=True)
        try:
            directory.resolve().relative_to(self.root)
        except ValueError as exc:
            raise ValueError("服务器 workspace 路径不能离开 workspace") from exc
        return directory

    def _path(self, value: Any, *, allow_root: bool = False, root: Path | None = None) -> Path:
        workspace_root = (root or self.root).resolve()
        raw = str(value or "").strip()
        if "\x00" in raw:
            raise ValueError("本地路径包含无效字符")
        relative = Path(raw or ".")
        if relative.is_absolute():
            raise ValueError("本地路径必须相对于 workspace")
        candidate = (workspace_root / relative).resolve()
        try:
            candidate.relative_to(workspace_root)
        except ValueError as exc:
            raise ValueError("本地路径不能离开 workspace") from exc
        if candidate == workspace_root and not allow_root:
            raise ValueError("请选择 workspace 中的文件或子目录")
        return candidate

    def _relative(self, path: Path, root: Path | None = None) -> str:
        value = path.relative_to(root or self.root).as_posix()
        return value or "."

    def list(self, path: Any = ".", *, root: Path | None = None) -> dict[str, Any]:
        directory = self._path(path, allow_root=True, root=root)
        if not directory.exists():
            raise FileNotFoundError(f"本地目录不存在：{self._relative(directory, root)}")
        if not directory.is_dir():
            raise NotADirectoryError(f"不是本地目录：{self._relative(directory, root)}")
        entries = []
        for child in sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            try:
                child.resolve().relative_to(root or self.root)
            except ValueError:
                continue
            info = child.stat()
            entries.append({
                "name": child.name,
                "path": self._relative(child, root),
                "type": "directory" if child.is_dir() else "file",
                "size": info.st_size,
                "modified_at": datetime.fromtimestamp(info.st_mtime).astimezone().isoformat(timespec="seconds"),
            })
            if len(entries) >= MAX_LIST_ITEMS:
                break
        return {"path": self._relative(directory, root), "entries": entries, "truncated": len(entries) >= MAX_LIST_ITEMS}

    def read(self, path: Any, *, root: Path | None = None) -> dict[str, Any]:
        target = self._path(path, root=root)
        if not target.is_file():
            raise FileNotFoundError(f"本地文件不存在：{self._relative(target, root)}")
        size = target.stat().st_size
        if size > MAX_WORKSPACE_TEXT:
            raise ValueError("workspace_read 仅支持不超过 256 KiB 的文本文件")
        raw = target.read_bytes()
        if b"\x00" in raw:
            raise ValueError("本地文件是二进制内容，请使用 sftp_transfer 传输")
        try:
            content = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("本地文件不是 UTF-8 文本") from exc
        return {"path": self._relative(target, root), "content": content, "size": size}

    def write(self, path: Any, content: Any, overwrite: bool = False, *, root: Path | None = None) -> dict[str, Any]:
        target = self._path(path, root=root)
        text = str(content if content is not None else "")
        raw = text.encode("utf-8")
        if len(raw) > MAX_WORKSPACE_TEXT:
            raise ValueError("workspace_write 单个文件不能超过 256 KiB")
        if target.exists() and not overwrite:
            raise FileExistsError("本地文件已存在；确认后可设置 overwrite=true")
        if target.exists() and not target.is_file():
            raise IsADirectoryError(f"目标不是文件：{self._relative(target, root)}")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.agent-{secrets.token_hex(6)}.tmp")
        try:
            temporary.write_bytes(raw)
            if overwrite:
                os.replace(temporary, target)
            else:
                os.link(temporary, target)
                temporary.unlink()
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return {"path": self._relative(target, root), "size": len(raw), "overwritten": bool(overwrite)}

    def sftp_transfer(
        self, session_id: str, direction: Any, local_path: Any, remote_path: Any, overwrite: bool = False
    ) -> dict[str, Any]:
        operation = str(direction or "").strip().lower()
        root = self._session_root(session_id)
        local = self._path(local_path, root=root)
        remote = clean_remote_path(str(remote_path or "").strip())
        if operation == "upload":
            return self._upload(session_id, local, remote, overwrite, root)
        if operation == "download":
            return self._download(session_id, local, remote, overwrite, root)
        raise ValueError("direction 必须是 upload 或 download")

    def _upload(self, session_id: str, local: Path, remote: str, overwrite: bool, root: Path) -> dict[str, Any]:
        if not local.is_file():
            raise FileNotFoundError(f"本地文件不存在：{self._relative(local, root)}")
        size = local.stat().st_size
        if size > MAX_SFTP_TRANSFER:
            raise ValueError("Agent SFTP 单个文件不能超过 512 MiB")
        sftp = self.session_getter(session_id).sftp
        if not overwrite:
            try:
                sftp.lstat(remote)
            except FileNotFoundError:
                pass
            else:
                raise FileExistsError("远端文件已存在；确认后可设置 overwrite=true")
        directory, name = posixpath.dirname(remote) or ".", posixpath.basename(remote)
        if not name or name in (".", ".."):
            raise ValueError("远端目标必须是完整文件路径")
        temporary = posixpath.join(directory, f".{name}.agent-{secrets.token_hex(6)}.tmp")
        try:
            with local.open("rb") as source, sftp.open(temporary, "wb") as destination:
                if hasattr(destination, "set_pipelined"):
                    destination.set_pipelined(True)
                while chunk := source.read(TRANSFER_CHUNK):
                    destination.write(chunk)
            try:
                sftp.posix_rename(temporary, remote)
            except (AttributeError, OSError):
                if overwrite:
                    try:
                        sftp.remove(remote)
                    except FileNotFoundError:
                        pass
                sftp.rename(temporary, remote)
        except Exception:
            try:
                sftp.remove(temporary)
            except OSError:
                pass
            raise
        return {
            "direction": "upload", "local_path": self._relative(local, root), "remote_path": remote, "size": size
        }

    def _download(self, session_id: str, local: Path, remote: str, overwrite: bool, root: Path) -> dict[str, Any]:
        if local.exists() and not overwrite:
            raise FileExistsError("本地文件已存在；确认后可设置 overwrite=true")
        if local.exists() and not local.is_file():
            raise IsADirectoryError(f"本地目标不是文件：{self._relative(local, root)}")
        sftp = self.session_getter(session_id).sftp
        info = sftp.stat(remote)
        if stat.S_ISDIR(info.st_mode):
            raise IsADirectoryError("Agent SFTP 暂不支持传输目录")
        if info.st_size > MAX_SFTP_TRANSFER:
            raise ValueError("Agent SFTP 单个文件不能超过 512 MiB")
        local.parent.mkdir(parents=True, exist_ok=True)
        temporary = local.with_name(f".{local.name}.agent-{secrets.token_hex(6)}.tmp")
        written = 0
        try:
            with sftp.open(remote, "rb") as source, temporary.open("xb") as destination:
                while chunk := source.read(TRANSFER_CHUNK):
                    destination.write(chunk)
                    written += len(chunk)
                    if written > MAX_SFTP_TRANSFER:
                        raise ValueError("Agent SFTP 单个文件不能超过 512 MiB")
            if overwrite:
                os.replace(temporary, local)
            else:
                os.link(temporary, local)
                temporary.unlink()
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return {
            "direction": "download", "local_path": self._relative(local, root), "remote_path": remote, "size": written
        }

    def execute(self, session_id: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        root = self._session_root(session_id)
        if tool_name == "workspace_list":
            return self.list(arguments.get("path", "."), root=root)
        if tool_name == "workspace_read":
            return self.read(arguments.get("path"), root=root)
        if tool_name == "workspace_write":
            return self.write(arguments.get("path"), arguments.get("content"), bool(arguments.get("overwrite", False)), root=root)
        if tool_name == "sftp_transfer":
            return self.sftp_transfer(
                session_id,
                arguments.get("direction"),
                arguments.get("local_path"),
                arguments.get("remote_path"),
                bool(arguments.get("overwrite", False)),
            )
        raise ValueError("不支持的本地工具")
