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
MAX_AGENT_INSTRUCTIONS = 64 * 1024
MAX_SFTP_TRANSFER = 512 * 1024 * 1024
MAX_LIST_ITEMS = 500
TRANSFER_CHUNK = 256 * 1024
AGENT_INSTRUCTIONS_FILE = "AGENTS.md"
MANAGED_START = "<!-- coshell:host-info:start -->"
MANAGED_END = "<!-- coshell:host-info:end -->"

HOST_DISCOVERY_COMMAND = r"""LC_ALL=C sh -c '
section() { printf "\n__COSHELL_%s__\n" "$1"; }
section HOSTNAME; (hostname -f 2>/dev/null || hostname 2>/dev/null || true)
section OS; (if [ -r /etc/os-release ]; then cat /etc/os-release; else uname -s 2>/dev/null; fi)
section KERNEL; (uname -srmo 2>/dev/null || uname -a 2>/dev/null || true)
section IDENTITY; (printf "user=%s\n" "$(id -un 2>/dev/null || whoami 2>/dev/null)"; printf "home=%s\n" "$HOME"; printf "shell=%s\n" "$SHELL")
section SERVICES; (if command -v systemctl >/dev/null 2>&1; then systemctl list-units --type=service --state=running --no-legend --no-pager 2>/dev/null | head -n 100; elif command -v rc-status >/dev/null 2>&1; then rc-status 2>/dev/null | head -n 100; elif command -v service >/dev/null 2>&1; then service --status-all 2>/dev/null | head -n 100; fi)
section CONTAINERS; (if command -v docker >/dev/null 2>&1; then docker ps --format "{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null | head -n 50; elif command -v podman >/dev/null 2>&1; then podman ps --format "{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null | head -n 50; fi)
section PORTS; (if command -v ss >/dev/null 2>&1; then ss -lntupH 2>/dev/null | head -n 100; elif command -v netstat >/dev/null 2>&1; then netstat -lntup 2>/dev/null | head -n 100; fi)
'"""

WINDOWS_HOST_DISCOVERY_COMMAND = r"""powershell.exe -NoProfile -NonInteractive -Command "$ErrorActionPreference='SilentlyContinue'; function Section([string]$Name) { Write-Output ('__COSHELL_' + $Name + '__') }; Section 'HOSTNAME'; [Environment]::MachineName; Section 'OS'; $Os=Get-CimInstance Win32_OperatingSystem; Write-Output ('PRETTY_NAME=' + $Os.Caption + ' ' + $Os.Version); Section 'KERNEL'; Write-Output ([Environment]::OSVersion.VersionString + ' ' + [Environment]::Is64BitOperatingSystem); Section 'IDENTITY'; Write-Output ('user=' + [Environment]::UserName); Write-Output ('home=' + $HOME); Write-Output ('shell=PowerShell'); Section 'SERVICES'; Get-Service | Where-Object Status -eq 'Running' | Select-Object -First 100 | ForEach-Object { Write-Output ($_.Name + \"`t\" + $_.DisplayName + \"`tRunning\") }; Section 'CONTAINERS'; if (Get-Command docker -ErrorAction SilentlyContinue) { docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' 2>$null | Select-Object -First 50 } elseif (Get-Command podman -ErrorAction SilentlyContinue) { podman ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' 2>$null | Select-Object -First 50 }; Section 'PORTS'; Get-NetTCPConnection -State Listen | Select-Object -First 100 | ForEach-Object { Write-Output ($_.LocalAddress + ':' + $_.LocalPort + \"`t\" + $_.OwningProcess) }" """


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

    def agent_instructions(self, session_id: str) -> str | None:
        """Return the current host instructions without creating a workspace."""
        root = self._session_root(session_id)
        target = root / AGENT_INSTRUCTIONS_FILE
        if not target.is_file():
            return None
        raw = target.read_bytes()
        if b"\x00" in raw:
            return None
        truncated = len(raw) > MAX_AGENT_INSTRUCTIONS
        try:
            content = raw[:MAX_AGENT_INSTRUCTIONS].decode("utf-8-sig", "strict")
        except UnicodeDecodeError:
            content = raw[:MAX_AGENT_INSTRUCTIONS].decode("utf-8", "replace")
        content = content.replace("\r\n", "\n").replace("\r", "\n")
        if truncated:
            content = content.rstrip() + "\n\n> [CoShell] 文件内容过长，注入系统提示时已截断。"
        return content

    @staticmethod
    def _parse_discovery_output(output: str) -> dict[str, str]:
        sections: dict[str, list[str]] = {}
        current: str | None = None
        for line in output[:256 * 1024].splitlines():
            if line.startswith("__COSHELL_") and line.endswith("__"):
                current = line.removeprefix("__COSHELL_").removesuffix("__")
                sections.setdefault(current, [])
            elif current:
                sections[current].append(line.rstrip())
        return {key: "\n".join(value).strip() for key, value in sections.items()}

    @staticmethod
    def _os_name(os_release: str) -> str:
        values: dict[str, str] = {}
        for line in os_release.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key.strip().upper()] = value.strip().strip("'\"")
        if not os_release:
            return "未知"
        return values.get("PRETTY_NAME") or values.get("NAME") or os_release.splitlines()[0]

    @staticmethod
    def _managed_block(session: SSHSession, facts: dict[str, str]) -> str:
        collected_at = datetime.now().astimezone().isoformat(timespec="seconds")
        hostname = facts.get("HOSTNAME") or session.host
        os_name = AgentWorkspace._os_name(facts.get("OS", ""))
        identity: dict[str, str] = {}
        for line in facts.get("IDENTITY", "").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                identity[key] = value

        def text_block(value: str, empty: str = "未检测到或当前用户无权查看") -> str:
            return f"```text\n{value or empty}\n```"

        return "\n".join([
            MANAGED_START,
            "## CoShell 自动维护的主机信息",
            "",
            "> 此区块由“初始化主机”工具维护。再次初始化会刷新此区块，请把手写内容放在区块外。",
            "",
            f"- 连接地址：`{session.host}:{session.port}`",
            f"- SSH 用户：`{session.username}`",
            f"- 主机名：`{hostname}`",
            f"- 操作系统：`{os_name}`",
            f"- 内核与架构：`{facts.get('KERNEL') or '未知'}`",
            f"- 远端用户：`{identity.get('user') or session.username}`",
            f"- 远端主目录：`{identity.get('home') or '未知'}`",
            f"- 默认 Shell：`{identity.get('shell') or '未知'}`",
            f"- 最近采集：`{collected_at}`",
            "",
            "### 正在运行的系统服务",
            "",
            text_block(facts.get("SERVICES", "")),
            "",
            "### 正在运行的容器",
            "",
            text_block(facts.get("CONTAINERS", ""), "未检测到、未安装容器运行时或当前用户无权查看"),
            "",
            "### 监听端口",
            "",
            text_block(facts.get("PORTS", "")),
            MANAGED_END,
        ])

    @staticmethod
    def _merge_managed_block(existing: str, managed: str) -> str:
        start = existing.find(MANAGED_START)
        end = existing.find(MANAGED_END, start + len(MANAGED_START)) if start >= 0 else -1
        if start >= 0 and end >= 0:
            end += len(MANAGED_END)
            return (existing[:start].rstrip() + "\n\n" + managed + "\n\n" + existing[end:].lstrip()).rstrip() + "\n"
        preserved = existing.replace(MANAGED_START, "").replace(MANAGED_END, "").strip()
        if preserved:
            return preserved + "\n\n" + managed + "\n"
        return managed + "\n"

    def initialize_host(self, session_id: str) -> dict[str, Any]:
        """Create or refresh the managed host section while preserving user content."""
        session = self.session_getter(session_id)
        root = self._session_root(session_id)
        target = root / AGENT_INSTRUCTIONS_FILE
        existed = target.exists()
        existing = ""
        if existed:
            if not target.is_file():
                raise IsADirectoryError(f"{AGENT_INSTRUCTIONS_FILE} 不是文件")
            raw = target.read_bytes()
            if len(raw) > MAX_WORKSPACE_TEXT:
                raise ValueError(f"{AGENT_INSTRUCTIONS_FILE} 超过 256 KiB，无法安全更新")
            try:
                existing = raw.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise ValueError(f"{AGENT_INSTRUCTIONS_FILE} 不是 UTF-8 文本") from exc

        error = ""
        facts: dict[str, str] = {}
        for command in (HOST_DISCOVERY_COMMAND, WINDOWS_HOST_DISCOVERY_COMMAND):
            try:
                _, stdout, stderr = session.client.exec_command(command, timeout=20)
                output = stdout.read(256 * 1024).decode("utf-8", "replace")
                error = stderr.read(8192).decode("utf-8", "replace").strip() or error
            except Exception as exc:
                error = str(exc)
                continue
            facts = self._parse_discovery_output(output)
            if facts:
                break
        if not facts:
            raise OSError(error or "远端主机未返回可识别的初始化信息")
        managed = self._managed_block(session, facts)
        if existing:
            content = self._merge_managed_block(existing, managed)
        else:
            content = "\n".join([
                "# 主机 Agent 指南",
                "",
                "本文件会自动加入此主机 Agent 会话的系统提示。你可以自由编辑，并在自动维护区块外补充主机用途、部署约定、禁用操作和排障经验。",
                "",
                managed,
                "",
                "## 用户维护信息",
                "",
                "- 主机用途：",
                "- 重要目录或项目：",
                "- 部署与运维约定：",
                "- 禁止或需谨慎执行的操作：",
                "",
            ])
        result = self.write(AGENT_INSTRUCTIONS_FILE, content, overwrite=existed, root=root)
        return {
            **result,
            "created": not existed,
            "updated": existed,
            "hostname": facts.get("HOSTNAME") or session.host,
            "service_count": len(facts.get("SERVICES", "").splitlines()),
            "container_count": len(facts.get("CONTAINERS", "").splitlines()),
            "port_count": len(facts.get("PORTS", "").splitlines()),
        }

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
        return self._sftp_transfer(
            session_id, direction, local_path, remote_path, overwrite, self._session_root(session_id)
        )

    def workspace_root_sftp_transfer(
        self, session_id: str, direction: Any, local_path: Any, remote_path: Any, overwrite: bool = False
    ) -> dict[str, Any]:
        return self._sftp_transfer(session_id, direction, local_path, remote_path, overwrite, self.root)

    def _sftp_transfer(
        self, session_id: str, direction: Any, local_path: Any, remote_path: Any, overwrite: bool, root: Path
    ) -> dict[str, Any]:
        operation = str(direction or "").strip().lower()
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
        if tool_name == "initialize_host_workspace":
            return self.initialize_host(session_id)
        if tool_name == "workspace_root_list":
            return self.list(arguments.get("path", "."), root=self.root)
        if tool_name == "workspace_root_read":
            return self.read(arguments.get("path"), root=self.root)
        if tool_name == "workspace_root_write":
            return self.write(
                arguments.get("path"), arguments.get("content"), bool(arguments.get("overwrite", False)), root=self.root
            )
        if tool_name == "workspace_root_sftp_transfer":
            return self.workspace_root_sftp_transfer(
                session_id,
                arguments.get("direction"),
                arguments.get("local_path"),
                arguments.get("remote_path"),
                bool(arguments.get("overwrite", False)),
            )
        raise ValueError("不支持的本地工具")
