from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime, timezone
from typing import Any

from .database import Database


FORMAT = "light-ssh-terminal-backup"
VERSION = 1
MAX_BACKUP_BYTES = 16 * 1024 * 1024

# Order is significant: parent tables precede tables that reference them.
TABLES: dict[str, tuple[str, ...]] = {
    "vault": ("id", "salt", "verifier", "created_at"),
    "ssh_keys": ("id", "name", "key_type", "fingerprint", "private_key_enc", "passphrase_enc", "created_at"),
    "servers": ("id", "name", "host", "port", "username", "auth_type", "password_enc", "private_key_enc", "passphrase_enc", "note", "last_connected_at", "created_at", "updated_at", "ssh_key_id"),
    "shortcuts": ("id", "name", "command", "group_name", "sort_order", "created_at", "updated_at"),
    "host_keys": ("host", "port", "algorithm", "fingerprint", "key_base64", "trusted_at"),
    "terminal_tabs": ("id", "title", "server_id", "position", "last_path"),
    "settings": ("key", "value"),
    "agent_settings": ("id", "api_url", "api_key_enc", "model", "updated_at", "builtin_web_search"),
    "mcp_servers": ("id", "name", "url", "auth_token_enc", "enabled", "tools_json", "created_at", "updated_at"),
}


class BackupError(ValueError):
    pass


def _encode(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"$bytes": base64.b64encode(value).decode("ascii")}
    return value


def _decode(value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"$bytes"} and isinstance(value["$bytes"], str):
        try:
            return base64.b64decode(value["$bytes"], validate=True)
        except (ValueError, binascii.Error) as exc:
            raise BackupError("备份文件包含无效的二进制数据") from exc
    if isinstance(value, (dict, list)):
        raise BackupError("备份文件包含不支持的嵌套数据")
    return value


def create_backup(db: Database) -> bytes:
    tables = db.snapshot(TABLES)
    # Auto-unlock is protected by Windows DPAPI and must remain device-local.
    tables["settings"] = [row for row in tables["settings"] if row["key"] != "desktop_auto_unlock"]
    document = {
        "format": FORMAT,
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tables": {
            table: [{column: _encode(row[column]) for column in columns} for row in tables[table]]
            for table, columns in TABLES.items()
        },
    }
    return json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")


def parse_backup(content: bytes) -> dict[str, list[dict[str, Any]]]:
    if not content:
        raise BackupError("备份文件为空")
    if len(content) > MAX_BACKUP_BYTES:
        raise BackupError("备份文件不能超过 16 MiB")
    try:
        document = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError("请选择有效的 Light SSH Terminal JSON 备份文件") from exc
    if not isinstance(document, dict) or document.get("format") != FORMAT or document.get("version") != VERSION:
        raise BackupError("备份文件格式或版本不受支持")
    raw_tables = document.get("tables")
    if not isinstance(raw_tables, dict) or set(raw_tables) != set(TABLES):
        raise BackupError("备份文件的数据表不完整")
    result: dict[str, list[dict[str, Any]]] = {}
    for table, columns in TABLES.items():
        raw_rows = raw_tables[table]
        if not isinstance(raw_rows, list):
            raise BackupError(f"备份文件中的 {table} 数据无效")
        decoded: list[dict[str, Any]] = []
        for row in raw_rows:
            if not isinstance(row, dict) or set(row) != set(columns):
                raise BackupError(f"备份文件中的 {table} 字段不匹配")
            decoded.append({column: _decode(row[column]) for column in columns})
        result[table] = decoded
    result["settings"] = [row for row in result["settings"] if row["key"] != "desktop_auto_unlock"]
    return result


def restore_backup(db: Database, content: bytes) -> dict[str, int]:
    rows = parse_backup(content)
    try:
        db.replace_snapshot(TABLES, rows)
    except Exception as exc:
        raise BackupError("备份数据无法写入，当前数据未被更改") from exc
    return {table: len(items) for table, items in rows.items()}
