"""GitHub Release updater used by the packaged Windows application."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urlparse

from . import __version__


REPOSITORY_URL = "https://github.com/dreamhartley/CoShell"
RELEASES_URL = f"{REPOSITORY_URL}/releases"
LATEST_RELEASE_API = "https://api.github.com/repos/dreamhartley/CoShell/releases/latest"
MAX_RELEASE_BYTES = 1024 * 1024 * 1024
MAX_EXTRACTED_BYTES = 3 * 1024 * 1024 * 1024
_VERSION_PATTERN = re.compile(r"^[vV]?(\d+(?:\.\d+){0,3})(?:[-+].*)?$")


class UpdateError(RuntimeError):
    """An update could not be checked, downloaded, or prepared safely."""


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    size: int
    digest: str | None = None


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag: str
    title: str
    url: str
    published_at: str | None
    asset: ReleaseAsset | None


def is_packaged() -> bool:
    return bool(getattr(sys, "frozen", False))


def can_install_updates() -> bool:
    return is_packaged() and os.name == "nt"


def application_info() -> dict[str, Any]:
    return {
        "current_version": __version__,
        "repository_url": REPOSITORY_URL,
        "releases_url": RELEASES_URL,
        "packaged": is_packaged(),
        "can_install": can_install_updates(),
    }


def _version_key(value: str) -> tuple[int, int, int, int]:
    match = _VERSION_PATTERN.fullmatch(value.strip())
    if not match:
        raise UpdateError(f"无法识别发行版版本号：{value}")
    parts = [int(part) for part in match.group(1).split(".")]
    padded = (parts + [0] * 4)[:4]
    return padded[0], padded[1], padded[2], padded[3]


def is_newer_version(candidate: str, current: str = __version__) -> bool:
    return _version_key(candidate) > _version_key(current)


def _asset_score(asset: dict[str, Any]) -> int:
    name = str(asset.get("name", "")).lower()
    if not name.endswith(".zip"):
        return -1
    if any(token in name for token in ("arm64", "aarch64", "macos", "darwin", "linux", "source", "src")):
        return -1
    score = 1
    if "coshell" in name:
        score += 20
    if any(token in name for token in ("windows", "win64", "win-x64")):
        score += 8
    if any(token in name for token in ("x64", "amd64")):
        score += 4
    return score


def _select_windows_asset(assets: list[dict[str, Any]]) -> ReleaseAsset | None:
    candidates = sorted(assets, key=_asset_score, reverse=True)
    if not candidates or _asset_score(candidates[0]) < 1:
        return None
    value = candidates[0]
    name = str(value.get("name", ""))
    url = str(value.get("browser_download_url", ""))
    parsed = urlparse(url)
    expected_prefix = "/dreamhartley/CoShell/releases/download/"
    if parsed.scheme != "https" or parsed.hostname != "github.com" or not parsed.path.startswith(expected_prefix):
        raise UpdateError("GitHub Release 返回了不受信任的下载地址")
    try:
        size = int(value.get("size", 0))
    except (TypeError, ValueError) as exc:
        raise UpdateError("GitHub Release 资源大小无效") from exc
    if size < 0 or size > MAX_RELEASE_BYTES:
        raise UpdateError("GitHub Release 资源大小超出允许范围")
    digest = value.get("digest")
    return ReleaseAsset(name=name, url=url, size=size, digest=str(digest) if digest else None)


def _read_json_response(response: Any) -> dict[str, Any]:
    content = response.read(2 * 1024 * 1024 + 1)
    if len(content) > 2 * 1024 * 1024:
        raise UpdateError("GitHub Release 响应过大")
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("GitHub Release 返回了无效数据") from exc
    if not isinstance(value, dict):
        raise UpdateError("GitHub Release 返回了无效数据")
    return value


def _trusted_release_url(value: object, tag: str) -> str:
    fallback = f"{RELEASES_URL}/tag/{quote(tag, safe='')}"
    url = str(value or "")
    parsed = urlparse(url)
    repository_path = urlparse(REPOSITORY_URL).path.rstrip("/")
    if (
        parsed.scheme == "https"
        and parsed.hostname == "github.com"
        and (parsed.path == repository_path or parsed.path.startswith(repository_path + "/"))
    ):
        return url
    return fallback


def fetch_latest_release(timeout: float = 15) -> ReleaseInfo:
    request = urllib.request.Request(
        LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"CoShell/{__version__}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            value = _read_json_response(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("GitHub 仓库中还没有可用的正式发行版") from exc
        if exc.code == 403:
            raise UpdateError("GitHub 暂时限制了更新检查，请稍后重试") from exc
        raise UpdateError(f"检查更新失败（GitHub HTTP {exc.code}）") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise UpdateError(f"无法连接 GitHub：{reason}") from exc

    tag = str(value.get("tag_name", "")).strip()
    version = tag[1:] if tag[:1].lower() == "v" else tag
    _version_key(version)
    assets = value.get("assets", [])
    if not isinstance(assets, list):
        raise UpdateError("GitHub Release 资源列表无效")
    return ReleaseInfo(
        version=version,
        tag=tag,
        title=str(value.get("name") or tag),
        url=_trusted_release_url(value.get("html_url"), tag),
        published_at=str(value["published_at"]) if value.get("published_at") else None,
        asset=_select_windows_asset([item for item in assets if isinstance(item, dict)]),
    )


def public_release_status(release: ReleaseInfo) -> dict[str, Any]:
    result = application_info()
    update_available = is_newer_version(release.version)
    result.update(
        {
            "checked": True,
            "latest_version": release.version,
            "release_name": release.title,
            "release_url": release.url,
            "published_at": release.published_at,
            "update_available": update_available,
            "asset_available": release.asset is not None,
            "asset_name": release.asset.name if release.asset else None,
            "asset_size": release.asset.size if release.asset else None,
            "can_install": can_install_updates() and update_available and release.asset is not None,
        }
    )
    return result


def _safe_extract_release(archive: Path, destination: Path) -> Path:
    total_size = 0
    try:
        with zipfile.ZipFile(archive) as package:
            members = package.infolist()
            if not members:
                raise UpdateError("下载的发行版压缩包为空")
            for member in members:
                raw_name = member.filename.replace("\\", "/")
                path = PurePosixPath(raw_name)
                if path.is_absolute() or not path.parts or ".." in path.parts or ":" in path.parts[0]:
                    raise UpdateError("发行版压缩包包含不安全的文件路径")
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise UpdateError("发行版压缩包不能包含符号链接")
                total_size += max(0, member.file_size)
                if total_size > MAX_EXTRACTED_BYTES:
                    raise UpdateError("发行版解压后的大小超出允许范围")
                target = destination.joinpath(*path.parts)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
    except UpdateError:
        raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, OSError) as exc:
        raise UpdateError(f"无法解压发行版：{exc}") from exc

    executable_candidates = [
        path for path in destination.rglob("CoShell.exe")
        if path.is_file() and (path.parent / "_internal").is_dir()
    ]
    if len(executable_candidates) != 1:
        raise UpdateError("发行版压缩包结构无效，未找到完整的 CoShell 程序")
    return executable_candidates[0].parent


def _download_asset(asset: ReleaseAsset, destination: Path, timeout: float = 45) -> None:
    request = urllib.request.Request(
        asset.url,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": f"CoShell/{__version__}",
        },
    )
    digest = hashlib.sha256()
    downloaded = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, destination.open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > MAX_RELEASE_BYTES:
                    raise UpdateError("下载的发行版文件过大")
                digest.update(chunk)
                output.write(chunk)
    except UpdateError:
        raise
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise UpdateError(f"下载更新失败：{reason}") from exc
    if asset.size and downloaded != asset.size:
        raise UpdateError(f"更新下载不完整（应为 {asset.size} 字节，实际 {downloaded} 字节）")
    if asset.digest:
        algorithm, separator, expected = asset.digest.partition(":")
        if separator and algorithm.lower() == "sha256" and digest.hexdigest().lower() != expected.lower():
            raise UpdateError("更新包 SHA-256 校验失败")


_UPDATER_SCRIPT = r"""
param(
    [Parameter(Mandatory=$true)][int]$ParentProcessId,
    [Parameter(Mandatory=$true)][string]$Source,
    [Parameter(Mandatory=$true)][string]$Target,
    [Parameter(Mandatory=$true)][string]$Executable,
    [Parameter(Mandatory=$true)][string]$Workspace
)
$ErrorActionPreference = "Stop"
$targetPath = [IO.Path]::GetFullPath($Target)
$sourcePath = [IO.Path]::GetFullPath($Source)
$rootPath = [IO.Path]::GetPathRoot($targetPath)
if ($targetPath.TrimEnd('\') -eq $rootPath.TrimEnd('\')) { throw "Refusing to update a drive root" }
$dataPath = Join-Path $targetPath "data"
New-Item -ItemType Directory -Path $dataPath -Force | Out-Null
$logPath = Join-Path $dataPath "update.log"
function Write-UpdateLog([string]$Message) {
    Add-Content -LiteralPath $logPath -Encoding UTF8 -Value ("{0:u} {1}" -f (Get-Date), $Message)
}
function Clear-ApplicationFiles {
    Get-ChildItem -LiteralPath $targetPath -Force |
        Where-Object { $_.Name -ne "data" } |
        Remove-Item -Recurse -Force
}

$backupPath = Join-Path (Split-Path -Parent $targetPath) (".CoShell-update-backup-" + [guid]::NewGuid().ToString("N"))
$oldMoved = $false
$parentExited = $false
try {
    $deadline = (Get-Date).AddSeconds(120)
    while (Get-Process -Id $ParentProcessId -ErrorAction SilentlyContinue) {
        if ((Get-Date) -gt $deadline) { throw "CoShell did not exit in time" }
        Start-Sleep -Milliseconds 250
    }
    $parentExited = $true
    New-Item -ItemType Directory -Path $backupPath | Out-Null
    Get-ChildItem -LiteralPath $targetPath -Force |
        Where-Object { $_.Name -ne "data" } |
        Move-Item -Destination $backupPath -Force
    $oldMoved = $true
    Get-ChildItem -LiteralPath $sourcePath -Force |
        Where-Object { $_.Name -ne "data" } |
        Copy-Item -Destination $targetPath -Recurse -Force
    $newExecutable = Join-Path $targetPath $Executable
    if (-not (Test-Path -LiteralPath $newExecutable -PathType Leaf)) {
        throw "The updated executable is missing"
    }
    Write-UpdateLog "Update files installed; starting $newExecutable"
    $process = Start-Process -FilePath $newExecutable -WorkingDirectory $targetPath -PassThru
    Start-Sleep -Seconds 5
    if ($process.HasExited) {
        throw "The updated application exited during startup"
    }
    Remove-Item -LiteralPath $backupPath -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $Workspace -Recurse -Force -ErrorAction SilentlyContinue
    Write-UpdateLog "Update completed successfully"
} catch {
    Write-UpdateLog ("Update failed: " + $_.Exception.Message)
    if ($oldMoved) {
        Clear-ApplicationFiles
    }
    if (Test-Path -LiteralPath $backupPath -PathType Container) {
        Get-ChildItem -LiteralPath $backupPath -Force -ErrorAction SilentlyContinue |
            Move-Item -Destination $targetPath -Force
    }
    if ($parentExited) {
        $restoredExecutable = Join-Path $targetPath $Executable
        if (Test-Path -LiteralPath $restoredExecutable -PathType Leaf) {
            Start-Process -FilePath $restoredExecutable -WorkingDirectory $targetPath
            Write-UpdateLog "Previous version restored and restarted"
        }
    }
    exit 1
}
"""


class UpdateManager:
    """Serialize update work and retain the trusted release details internally."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._release: ReleaseInfo | None = None
        self._workspace: Path | None = None
        self._payload: Path | None = None

    def check(self) -> dict[str, Any]:
        with self._lock:
            self._release = fetch_latest_release()
            return public_release_status(self._release)

    def prepare(self) -> dict[str, Any]:
        if not can_install_updates():
            raise UpdateError("自动安装仅可在 Windows 发行版中使用")
        with self._lock:
            release = fetch_latest_release()
            self._release = release
            if not is_newer_version(release.version):
                raise UpdateError("当前已经是最新版本")
            if release.asset is None:
                raise UpdateError("此发行版没有可用的 Windows ZIP 安装包")

            if self._workspace is not None:
                shutil.rmtree(self._workspace, ignore_errors=True)
            workspace = Path(tempfile.mkdtemp(prefix="coshell-update-")).resolve()
            archive = workspace / "release.zip"
            extracted = workspace / "extracted"
            extracted.mkdir()
            try:
                _download_asset(release.asset, archive)
                payload = _safe_extract_release(archive, extracted)
                script = workspace / "apply-update.ps1"
                script.write_text(_UPDATER_SCRIPT.strip() + "\n", encoding="utf-8-sig")
            except Exception:
                shutil.rmtree(workspace, ignore_errors=True)
                raise
            self._workspace = workspace
            self._payload = payload
            return {
                **public_release_status(release),
                "prepared": True,
            }

    def launch(self) -> None:
        if not can_install_updates():
            raise UpdateError("自动安装仅可在 Windows 发行版中使用")
        with self._lock:
            if self._workspace is None or self._payload is None:
                raise UpdateError("更新尚未下载完成")
            install_directory = Path(sys.executable).resolve().parent
            executable = Path(sys.executable).name
            if install_directory == Path(install_directory.anchor) or not (install_directory / executable).is_file():
                raise UpdateError("无法确认当前发行版安装目录")
            script = self._workspace / "apply-update.ps1"
            command = [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-File",
                str(script),
                "-ParentProcessId",
                str(os.getpid()),
                "-Source",
                str(self._payload),
                "-Target",
                str(install_directory),
                "-Executable",
                executable,
                "-Workspace",
                str(self._workspace),
            ]
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                subprocess.Popen(
                    command,
                    cwd=str(self._workspace),
                    creationflags=creation_flags,
                    close_fds=True,
                )
            except OSError as exc:
                raise UpdateError(f"无法启动更新安装程序：{exc}") from exc
