from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS vault (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    salt BLOB NOT NULL,
    verifier BLOB NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS servers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER NOT NULL DEFAULT 22,
    username TEXT NOT NULL,
    auth_type TEXT NOT NULL DEFAULT 'password',
    password_enc BLOB,
    private_key_enc BLOB,
    passphrase_enc BLOB,
    note TEXT NOT NULL DEFAULT '',
    last_connected_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS shortcuts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    command TEXT NOT NULL,
    group_name TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS host_keys (
    host TEXT NOT NULL,
    port INTEGER NOT NULL,
    algorithm TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    key_base64 TEXT NOT NULL,
    trusted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (host, port)
);
CREATE TABLE IF NOT EXISTS terminal_tabs (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    server_id INTEGER,
    position INTEGER NOT NULL DEFAULT 0,
    last_path TEXT NOT NULL DEFAULT '.',
    FOREIGN KEY(server_id) REFERENCES servers(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    api_url TEXT NOT NULL,
    api_key_enc BLOB,
    model TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS ssh_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    key_type TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    private_key_enc BLOB NOT NULL,
    passphrase_enc BLOB,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS mcp_servers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    url TEXT NOT NULL,
    auth_token_enc BLOB,
    enabled INTEGER NOT NULL DEFAULT 1,
    tools_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Apply small additive migrations to databases created by older versions."""
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(servers)")}
        if "ssh_key_id" not in columns:
            self._conn.execute("ALTER TABLE servers ADD COLUMN ssh_key_id INTEGER")
        if "last_connected_at" not in columns:
            self._conn.execute("ALTER TABLE servers ADD COLUMN last_connected_at TEXT")
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(agent_settings)")}
        if "builtin_web_search" not in columns:
            self._conn.execute("ALTER TABLE agent_settings ADD COLUMN builtin_web_search INTEGER NOT NULL DEFAULT 1")
        self._conn.commit()

    def execute(self, sql: str, values: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, tuple(values))
            self._conn.commit()
            return cur

    def fetchone(self, sql: str, values: Iterable[Any] = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(sql, tuple(values)).fetchone()
            return dict(row) if row else None

    def fetchall(self, sql: str, values: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._conn.execute(sql, tuple(values)).fetchall()]

    def snapshot(self, tables: dict[str, tuple[str, ...]]) -> dict[str, list[dict[str, Any]]]:
        """Read a consistent snapshot of a fixed set of tables."""
        with self._lock:
            result: dict[str, list[dict[str, Any]]] = {}
            for table, columns in tables.items():
                selected = ",".join(f'"{column}"' for column in columns)
                result[table] = [dict(row) for row in self._conn.execute(f'SELECT {selected} FROM "{table}"')]
            return result

    def replace_snapshot(self, tables: dict[str, tuple[str, ...]], rows: dict[str, list[dict[str, Any]]]) -> None:
        """Atomically replace a fixed set of tables with validated snapshot rows."""
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                for table in reversed(tuple(tables)):
                    self._conn.execute(f'DELETE FROM "{table}"')
                for table, columns in tables.items():
                    if not rows[table]:
                        continue
                    names = ",".join(f'"{column}"' for column in columns)
                    placeholders = ",".join("?" for _ in columns)
                    self._conn.executemany(
                        f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})',
                        ([row[column] for column in columns] for row in rows[table]),
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            self._conn.close()
