from __future__ import annotations

import re
import threading
import uuid
from dataclasses import dataclass

from .agent import AgentCancelled


@dataclass(frozen=True)
class CommandRisk:
    category: str
    reason: str


# These patterns intentionally match command segments on POSIX shells, cmd.exe,
# and PowerShell. They are conservative: an extra approval is preferable to an
# irreversible command slipping through because the remote OS was not detected.
_RISK_PATTERNS: tuple[tuple[re.Pattern[str], CommandRisk], ...] = (
    (re.compile(r"(?im)(?:^|[;&|]\s*)(?:(?:sudo|doas)\s+)?(?:rm|rmdir|unlink|shred)\b|\b(?:sudo|doas|sh|bash|zsh|fish)\b[^\r\n;&|]*\b(?:rm|rmdir|unlink|shred)\b|\bfind\b[^\r\n;&|]*\s-delete\b|\bxargs\b[^\r\n;&|]*\b(?:rm|rmdir)\b"), CommandRisk("delete", "该命令会删除文件或目录")),
    (re.compile(r"(?im)(?:^|[;&|]\s*)(?:del|erase|rd|rmdir)\b|\bcmd(?:\.exe)?\s+/(?:c|k)\s+(?:del|erase|rd|rmdir)\b|\b(?:remove-item|clear-content)\b|\breg(?:\.exe)?\s+delete\b|\b(?:remove-localuser|remove-localgroup)\b"), CommandRisk("delete", "该命令会删除 Windows 文件、目录、注册表项或账户")),
    (re.compile(r"(?im)\b(?:robocopy|rsync)\b[^\r\n]*(?:/mir\b|--delete(?:-\w+)?\b)"), CommandRisk("delete", "同步命令可能删除目标端的现有文件")),
    (re.compile(r"(?im)(?:^|[;&|]\s*)(?:(?:sudo|doas)\s+)?(?:mkfs(?:\.\w+)?|wipefs|fdisk|parted)\b|\bdd\b[^\r\n;&|]*\bof\s*=\s*/dev/|\b(?:format-volume|clear-disk|initialize-disk|diskpart)\b"), CommandRisk("disk", "该命令可能格式化磁盘、覆盖分区或破坏文件系统")),
    (re.compile(r"(?im)(?:^|[;&|]\s*)(?:(?:sudo|doas)\s+)?(?:shutdown|reboot|poweroff|halt)\b|\bsystemctl\s+(?:reboot|poweroff|halt)\b|\b(?:restart-computer|stop-computer)\b|\bshutdown(?:\.exe)?\s+/(?:s|r|p)\b"), CommandRisk("power", "该命令会重启或关闭远程主机")),
    (re.compile(r"(?im)(?:^|[;&|]\s*)(?:(?:sudo|doas)\s+)?(?:userdel|groupdel|deluser|delgroup)\b|\bnet(?:\.exe)?\s+(?:user|localgroup)\b[^\r\n]*\s/delete\b"), CommandRisk("account", "该命令会删除系统账户或用户组")),
    (re.compile(r"(?im)(?:^|[;&|]\s*)(?:(?:sudo|doas)\s+)?(?:apt(?:-get)?|dnf|yum|zypper|pacman|apk)\s+(?:remove|purge|autoremove|erase)\b|\b(?:winget|choco)\s+(?:uninstall|remove)\b|\buninstall-package\b"), CommandRisk("software", "该命令会卸载软件包")),
    (re.compile(r"(?im)\bgit\s+(?:reset\s+--hard\b|clean\b[^\r\n]*(?:-[a-z]*f|--force)\b)|\b(?:docker|podman)\s+(?:system\s+prune|(?:container\s+)?rm)\b|\bkubectl\s+delete\b|\bhelm\s+uninstall\b|\bterraform\s+destroy\b"), CommandRisk("destructive", "该命令会不可逆地清理代码、容器或基础设施资源")),
    (re.compile(r"(?im)\b(?:drop\s+(?:database|schema|table)|truncate\s+table|delete\s+from)\b"), CommandRisk("database", "该命令可能删除数据库结构或数据")),
    (re.compile(r"(?im)(?:^|[;&|]\s*)(?:(?:sudo|doas)\s+)?(?:iptables|ip6tables)\s+(?:-F|--flush|-X|--delete-chain)\b|\bufw\s+(?:disable|reset)\b|\bnetsh\b[^\r\n]*\bfirewall\b[^\r\n]*\b(?:delete|reset|set)\b"), CommandRisk("network", "该命令会清空或重置防火墙规则，可能导致网络中断")),
)


def classify_dangerous_command(command: str) -> CommandRisk | None:
    """Return the first recognized destructive/high-impact command risk."""
    value = command.strip()
    if not value:
        return None
    for pattern, risk in _RISK_PATTERNS:
        if pattern.search(value):
            return risk
    return None


@dataclass
class _PendingApproval:
    session_id: str
    scope: str
    event: threading.Event
    decision: bool | None = None


class AgentApprovalRegistry:
    """Coordinate a streaming Agent worker with a separate UI response request."""

    def __init__(self) -> None:
        self._items: dict[str, _PendingApproval] = {}
        self._lock = threading.RLock()

    def create(self, session_id: str, scope: str = "sidebar") -> str:
        approval_id = uuid.uuid4().hex
        with self._lock:
            self._items[approval_id] = _PendingApproval(session_id, scope, threading.Event())
        return approval_id

    def resolve(self, session_id: str, approval_id: str, approved: bool) -> bool:
        with self._lock:
            pending = self._items.get(approval_id)
            if not pending or pending.session_id != session_id or pending.decision is not None:
                return False
            pending.decision = bool(approved)
            pending.event.set()
            return True

    def wait(self, approval_id: str, cancel_event: threading.Event | None = None) -> bool:
        while True:
            with self._lock:
                pending = self._items.get(approval_id)
            if not pending:
                return False
            if pending.event.wait(0.1):
                with self._lock:
                    completed = self._items.pop(approval_id, pending)
                return bool(completed.decision)
            if cancel_event and cancel_event.is_set():
                with self._lock:
                    self._items.pop(approval_id, None)
                raise AgentCancelled("Agent 任务已停止")

    def cancel_session(self, session_id: str, scope: str | None = None) -> None:
        with self._lock:
            pending = [
                item for item in self._items.values()
                if item.session_id == session_id and (scope is None or item.scope == scope)
            ]
            for item in pending:
                item.decision = False
                item.event.set()
