import asyncio
import io
import json
import sqlite3
import stat
import sys
import threading
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.database import Database
from app.ssh import HostKeyRequired, SSHSession, SessionRegistry, UploadRegistry, VerifiedHostKeyPolicy, clean_remote_path, create_ssh_key_pair, normalize_remote_os, parse_private_key, save_ssh_key_pair
from app.vault import Vault, VaultError
from app.agent import AgentCancelled, AgentError, AgentRegistry, QUICK_FIX_SYSTEM_PROMPT, _WebPageParser, _validate_public_url, list_models, model_request_options, normalize_api_base, openai_stream_request, openai_url, stream_chat_message, web_search
from app.agent_permissions import AgentApprovalRegistry, classify_dangerous_command
from app.schemas import SSHKeyGenerateBody, ServerBody, ShortcutBody
from app import main as main_app
from app.main import agent_message_with_terminal_context, agent_workspace_executor, agents, terminal_agents
from app.agent_workspace import AgentWorkspace
from app.searxng_backend import _write_settings
from app.mcp import normalize_mcp_url, search_tools
from app.device_secrets import protect as protect_device_secret, unprotect as unprotect_device_secret
from app.backup import BackupError, create_backup, parse_backup, restore_backup


def test_vault_encrypts_and_unlocks(tmp_path):
    db = Database(tmp_path / "test.db")
    vault = Vault(db)
    vault.initialize("correct horse battery staple")
    encrypted = vault.encrypt("top-secret")
    assert encrypted and b"top-secret" not in encrypted
    vault.lock()
    with pytest.raises(VaultError):
        vault.decrypt(encrypted)
    with pytest.raises(VaultError):
        vault.unlock("wrong password")
    vault.unlock("correct horse battery staple")
    assert vault.decrypt(encrypted) == "top-secret"
    db.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI only")
def test_device_secret_is_encrypted_and_round_trips():
    password = "device-bound-password"
    encrypted = protect_device_secret(password)
    assert password.encode() not in encrypted
    assert unprotect_device_secret(encrypted) == password


def test_database_never_stores_plain_secret(tmp_path):
    path = tmp_path / "test.db"
    db = Database(path)
    vault = Vault(db)
    vault.initialize("another secure password")
    cipher = vault.encrypt("do-not-store-me")
    db.execute("INSERT INTO servers(name,host,username,password_enc) VALUES(?,?,?,?)", ("one", "host", "user", cipher))
    row = db.fetchone("SELECT password_enc FROM servers")
    assert b"do-not-store-me" not in row["password_enc"]
    assert vault.decrypt(row["password_enc"]) == "do-not-store-me"
    db.close()


def test_backup_round_trip_keeps_encrypted_data_and_excludes_auto_unlock(tmp_path):
    source = Database(tmp_path / "source.db")
    vault = Vault(source)
    vault.initialize("portable master password")
    cipher = vault.encrypt("secret-password")
    source.execute("INSERT INTO servers(name,host,username,password_enc) VALUES(?,?,?,?)", ("prod", "example.test", "root", cipher))
    source.execute("INSERT INTO settings(key,value) VALUES('theme','ocean')")
    source.execute("INSERT INTO settings(key,value) VALUES('desktop_auto_unlock','device-only-secret')")
    content = create_backup(source)
    assert b"secret-password" not in content
    assert b"device-only-secret" not in content
    assert parse_backup(content)["servers"][0]["os_type"] == "default"

    legacy_document = json.loads(content)
    legacy_document["tables"]["servers"][0].pop("os_type")
    assert parse_backup(json.dumps(legacy_document).encode())["servers"][0]["os_type"] == "default"

    target = Database(tmp_path / "target.db")
    target.execute("INSERT INTO shortcuts(name,command) VALUES('old','false')")
    counts = restore_backup(target, content)
    assert counts["servers"] == 1
    assert target.fetchone("SELECT name,host FROM servers") == {"name": "prod", "host": "example.test"}
    assert target.fetchone("SELECT value FROM settings WHERE key='theme'") == {"value": "ocean"}
    assert target.fetchone("SELECT value FROM settings WHERE key='desktop_auto_unlock'") is None
    assert target.fetchone("SELECT id FROM shortcuts") is None
    restored_vault = Vault(target)
    restored_vault.unlock("portable master password")
    assert restored_vault.decrypt(target.fetchone("SELECT password_enc FROM servers")["password_enc"]) == "secret-password"
    source.close()
    target.close()


def test_invalid_backup_is_rejected_without_changing_database(tmp_path):
    db = Database(tmp_path / "test.db")
    db.execute("INSERT INTO shortcuts(name,command) VALUES('keep','uptime')")
    with pytest.raises(BackupError):
        restore_backup(db, b'{"format":"wrong"}')
    assert db.fetchone("SELECT name FROM shortcuts") == {"name": "keep"}
    db.close()


def test_shortcut_name_is_limited_to_30_characters():
    assert ShortcutBody(name="x" * 30, command="true").name == "x" * 30
    with pytest.raises(ValueError):
        ShortcutBody(name="x" * 31, command="true")


@pytest.mark.parametrize(("key_type", "rsa_bits", "expected"), [
    ("ed25519", 3072, "ssh-ed25519"),
    ("rsa", 2048, "ssh-rsa"),
])
def test_generated_ssh_key_pair_round_trips_and_saves_exclusively(tmp_path, key_type, rsa_bits, expected):
    private_key, public_key = create_ssh_key_pair(key_type, rsa_bits, "key password", "test key")
    assert parse_private_key(private_key, "key password").get_name() == expected
    assert public_key.startswith(expected + " ") and public_key.rstrip().endswith("test key")
    private_path, public_path = save_ssh_key_pair(tmp_path / "ssh-keys", "id_test", private_key, public_key)
    assert private_path.read_text(encoding="utf-8") == private_key
    assert public_path.read_text(encoding="utf-8") == public_key
    with pytest.raises(FileExistsError):
        save_ssh_key_pair(tmp_path / "ssh-keys", "id_test", private_key, public_key)


def test_generate_ssh_key_saves_under_data_and_auto_imports(monkeypatch, tmp_path):
    database = Database(tmp_path / "webssh.db")
    test_vault = Vault(database)
    test_vault.initialize("test master password")
    monkeypatch.setattr(main_app, "DATA", tmp_path)
    monkeypatch.setattr(main_app, "db", database)
    monkeypatch.setattr(main_app, "vault", test_vault)

    result = main_app.generate_ssh_key(SSHKeyGenerateBody(
        name="Generated key", file_name="id_generated", key_type="ed25519",
        passphrase="private password", auto_import=True,
    ))

    assert result["imported"]["name"] == "Generated key"
    assert result["private_key_path"] == str(tmp_path / "ssh-keys" / "id_generated")
    assert (tmp_path / "ssh-keys" / "id_generated.pub").read_text(encoding="utf-8").startswith("ssh-ed25519 ")
    row = database.fetchone("SELECT private_key_enc,passphrase_enc FROM ssh_keys WHERE name='Generated key'")
    assert parse_private_key(test_vault.decrypt(row["private_key_enc"]), test_vault.decrypt(row["passphrase_enc"])).get_name() == "ssh-ed25519"

    test_vault.lock()
    file_only = main_app.generate_ssh_key(SSHKeyGenerateBody(
        name="File only", file_name="id_file_only", auto_import=False,
    ))
    assert file_only["imported"] is None
    assert (tmp_path / "ssh-keys" / "id_file_only").is_file()
    assert database.fetchone("SELECT id FROM ssh_keys WHERE name='File only'") is None

    test_vault.unlock("test master password")
    automatic = main_app.generate_ssh_key(SSHKeyGenerateBody())
    assert automatic["name"] == automatic["file_name"]
    assert automatic["file_name"].startswith("id_ed25519_")
    assert (tmp_path / "ssh-keys" / automatic["file_name"]).is_file()
    assert automatic["imported"]["name"] == automatic["name"]
    database.close()


def test_saved_server_credentials_follow_selected_auth_type(monkeypatch, tmp_path):
    database = Database(tmp_path / "webssh.db")
    test_vault = Vault(database)
    test_vault.initialize("test master password")
    monkeypatch.setattr(main_app, "db", database)
    monkeypatch.setattr(main_app, "vault", test_vault)
    row = {
        "ssh_key_id": None,
        "password_enc": test_vault.encrypt("server password"),
        "private_key_enc": test_vault.encrypt("private key data"),
        "passphrase_enc": test_vault.encrypt("key passphrase"),
    }

    password_credentials = main_app.server_credentials({**row, "auth_type": "password"})
    assert password_credentials == {
        "password": "server password",
        "private_key": None,
        "passphrase": None,
    }

    key_credentials = main_app.server_credentials({**row, "auth_type": "private_key"})
    assert key_credentials == {
        "password": None,
        "private_key": "private key data",
        "passphrase": "key passphrase",
    }
    database.close()


@pytest.mark.parametrize(("raw", "expected"), [
    ("/home/user/../data", "/home/data"),
    ("./logs//today", "logs/today"),
    (r"home\user\file", "home/user/file"),
])
def test_remote_path_normalization(raw, expected):
    assert clean_remote_path(raw) == expected


def test_remote_path_rejects_nul():
    with pytest.raises(ValueError):
        clean_remote_path("/tmp/evil\x00name")


def test_session_registry_evicts_inactive_transport():
    class ClosedTransport:
        def is_active(self): return False
    class FakeClient:
        closed = False
        def get_transport(self): return ClosedTransport()
        def close(self): self.closed = True
    class Closable:
        def close(self): pass

    registry = SessionRegistry(None)
    client = FakeClient()
    registry._items["dead"] = SSHSession("dead", client, Closable(), Closable(), "host", 22, "user")
    with pytest.raises(KeyError, match="已断开"):
        registry.get("dead")
    assert "dead" not in registry._items
    assert client.closed


class FakeRemote:
    def __init__(self):
        self.data = bytearray()
        self.closed = False
    def set_pipelined(self, value): pass
    def write(self, chunk): self.data.extend(chunk)
    def flush(self): pass
    def close(self): self.closed = True


class FakeSftp:
    def __init__(self):
        self.remote = FakeRemote()
        self.removed = []
    def lstat(self, path): raise FileNotFoundError(path)
    def open(self, path, mode): return self.remote
    def remove(self, path): self.removed.append(path)


def test_chunk_upload_tracks_remote_progress():
    registry, sftp = UploadRegistry(), FakeSftp()
    item = registry.create("ssh-1", sftp, "/tmp/big.bin", 6, False)
    assert registry.write(item.id, 0, b"abc").written == 3
    with pytest.raises(ValueError):
        registry.write(item.id, 1, b"bad offset")
    assert registry.write(item.id, 3, b"def").written == 6
    finished = registry.finish(item.id)
    assert bytes(sftp.remote.data) == b"abcdef"
    assert finished.written == 6 and sftp.remote.closed


def test_aborted_upload_removes_partial_file():
    registry, sftp = UploadRegistry(), FakeSftp()
    item = registry.create("ssh-1", sftp, "/tmp/partial.bin", 10, False)
    registry.write(item.id, 0, b"abc")
    registry.abort(item.id)
    assert sftp.removed == ["/tmp/partial.bin"]


class MemorySftpFile(io.BytesIO):
    def __init__(self, initial=b"", on_close=None):
        super().__init__(initial)
        self.on_close = on_close
    def set_pipelined(self, _value): pass
    def close(self):
        if self.on_close and not self.closed:
            self.on_close(self.getvalue())
        super().close()


class MemorySftp:
    def __init__(self):
        self.files = {"/remote/source.bin": b"download-data"}
    def lstat(self, path):
        if path not in self.files: raise FileNotFoundError(path)
        return self.stat(path)
    def stat(self, path):
        if path not in self.files: raise FileNotFoundError(path)
        return SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_size=len(self.files[path]))
    def open(self, path, mode):
        if mode == "rb":
            if path not in self.files: raise FileNotFoundError(path)
            return MemorySftpFile(self.files[path])
        if mode == "wb":
            return MemorySftpFile(on_close=lambda value: self.files.__setitem__(path, value))
        raise ValueError(mode)
    def posix_rename(self, source, destination): self.files[destination] = self.files.pop(source)
    def rename(self, source, destination): self.posix_rename(source, destination)
    def remove(self, path):
        if path not in self.files: raise FileNotFoundError(path)
        del self.files[path]


def test_agent_workspace_is_sandboxed_and_supports_sftp(tmp_path):
    sftp = MemorySftp()
    session = SimpleNamespace(sftp=sftp, server_id=7, host="example.test", port=22, username="root")
    workspace = AgentWorkspace(tmp_path / "workspace", lambda _session_id: session)
    written = workspace.write("notes/info.txt", "hello", False)
    assert written["path"] == "notes/info.txt"
    assert workspace.read("notes/info.txt")["content"] == "hello"
    assert workspace.list("notes")["entries"][0]["name"] == "info.txt"
    with pytest.raises(ValueError, match="不能离开"):
        workspace.read("../secret.txt")
    with pytest.raises(FileExistsError):
        workspace.write("notes/info.txt", "replace", False)

    scoped = workspace.execute("ssh-1", "workspace_write", {"path": "notes/info.txt", "content": "hello"})
    assert scoped["path"] == "notes/info.txt"
    uploaded = workspace.sftp_transfer("ssh-1", "upload", "notes/info.txt", "/remote/info.txt", False)
    assert uploaded["size"] == 5 and sftp.files["/remote/info.txt"] == b"hello"
    downloaded = workspace.sftp_transfer("ssh-1", "download", "downloads/source.bin", "/remote/source.bin", False)
    assert downloaded["size"] == 13
    assert (tmp_path / "workspace" / "server-7" / "downloads" / "source.bin").read_bytes() == b"download-data"


def test_agent_workspace_is_isolated_per_server_and_can_be_deleted(tmp_path):
    sessions = {
        "one": SimpleNamespace(sftp=MemorySftp(), server_id=1, host="one.test", port=22, username="root"),
        "two": SimpleNamespace(sftp=MemorySftp(), server_id=2, host="two.test", port=22, username="root"),
    }
    workspace = AgentWorkspace(tmp_path / "workspace", sessions.__getitem__)

    workspace.execute("one", "workspace_write", {"path": "same.txt", "content": "server one"})
    workspace.execute("two", "workspace_write", {"path": "same.txt", "content": "server two"})

    assert workspace.execute("one", "workspace_read", {"path": "same.txt"})["content"] == "server one"
    assert workspace.execute("two", "workspace_read", {"path": "same.txt"})["content"] == "server two"
    assert workspace.server_workspace_exists(1)
    assert workspace.delete_server_workspace(1)
    assert not (tmp_path / "workspace" / "server-1").exists()
    assert (tmp_path / "workspace" / "server-2" / "same.txt").is_file()


def test_agent_workspace_root_tools_share_and_persist_files_across_servers(tmp_path):
    sessions = {
        "one": SimpleNamespace(sftp=MemorySftp(), server_id=1, host="one.test", port=22, username="root"),
        "two": SimpleNamespace(sftp=MemorySftp(), server_id=2, host="two.test", port=22, username="root"),
    }
    workspace = AgentWorkspace(tmp_path / "workspace", sessions.__getitem__)

    written = workspace.execute("one", "workspace_root_write", {
        "path": "shared/deploy.sh", "content": "#!/bin/sh\necho shared\n",
    })
    assert written["path"] == "shared/deploy.sh"
    assert workspace.execute("two", "workspace_root_read", {"path": "shared/deploy.sh"})["content"].endswith("echo shared\n")
    assert workspace.execute("two", "workspace_root_list", {"path": "shared"})["entries"][0]["name"] == "deploy.sh"

    uploaded = workspace.execute("two", "workspace_root_sftp_transfer", {
        "direction": "upload", "local_path": "shared/deploy.sh", "remote_path": "/tmp/deploy.sh",
    })
    assert uploaded["size"] == len(b"#!/bin/sh\necho shared\n")
    assert sessions["two"].sftp.files["/tmp/deploy.sh"].endswith(b"echo shared\n")

    workspace.execute("one", "workspace_write", {"path": "temporary.txt", "content": "temporary"})
    assert workspace.delete_server_workspace(1)
    assert (tmp_path / "workspace" / "shared" / "deploy.sh").is_file()
    with pytest.raises(ValueError, match="不能离开"):
        workspace.execute("two", "workspace_root_read", {"path": "../outside.txt"})


def test_workspace_root_access_approval_is_scoped_to_one_agent_task(monkeypatch):
    class FakeApprovals:
        def __init__(self):
            self.created = []
            self.waited = []

        def create(self, session_id, scope):
            self.created.append((session_id, scope))
            return "approval-id"

        def wait(self, approval_id, cancel_event):
            self.waited.append((approval_id, cancel_event))
            return True

    approvals = FakeApprovals()
    calls = []
    events = []
    monkeypatch.setattr("app.main.agent_approvals", approvals)
    monkeypatch.setattr("app.main.agent_workspace", SimpleNamespace(
        execute=lambda session_id, tool, arguments: calls.append((session_id, tool, arguments)) or {"path": arguments.get("path", ".")}
    ))

    execute = agent_workspace_executor("ssh-1", events.append)
    execute("workspace_list", {"path": "."})
    execute("workspace_root_list", {"path": "."})
    execute("workspace_root_read", {"path": "shared/deploy.sh"})

    assert approvals.created == [("ssh-1", "sidebar")]
    assert approvals.waited == [("approval-id", None)]
    assert [event["type"] for event in events] == ["command_approval_required", "command_approval_resolved"]
    assert events[0]["category"] == "workspace_root"
    assert len(calls) == 3


def test_workspace_root_access_rejection_never_calls_workspace(monkeypatch):
    approvals = SimpleNamespace(
        create=lambda _session_id, _scope: "approval-id",
        wait=lambda _approval_id, _cancel_event: False,
    )
    calls = []
    events = []
    monkeypatch.setattr("app.main.agent_approvals", approvals)
    monkeypatch.setattr("app.main.agent_workspace", SimpleNamespace(
        execute=lambda *args: calls.append(args)
    ))

    execute = agent_workspace_executor("ssh-1", events.append)
    with pytest.raises(PermissionError, match="用户拒绝"):
        execute("workspace_root_read", {"path": "shared/deploy.sh"})

    assert calls == []
    assert events[-1] == {"type": "command_approval_resolved", "approval_id": "approval-id", "approved": False}


def test_openai_url_accepts_compatible_base_url():
    assert openai_url("https://example.test/v1/", "models") == "https://example.test/v1/models"
    assert normalize_api_base("https://example.test/v1/chat/completions?ignored=1") == "https://example.test/v1"


def test_model_discovery_falls_back_to_v1(monkeypatch):
    def fake_request(base_url, *_args, **_kwargs):
        if base_url == "https://example.test":
            raise AgentError("not found", 404)
        return {"data": [{"id": "model-b"}, {"id": "model-a"}]}
    monkeypatch.setattr("app.agent.openai_request", fake_request)
    models, resolved = list_models("https://example.test", "key")
    assert models == ["model-a", "model-b"]
    assert resolved == "https://example.test/v1"


def test_terminal_and_sidebar_agent_conversations_are_isolated():
    assert agents is not terminal_agents
    assert "快速定位当前故障" in QUICK_FIX_SYSTEM_PROMPT
    assert "这不是普通聊天入口" in QUICK_FIX_SYSTEM_PROMPT
    assert "左侧 Agent" not in QUICK_FIX_SYSTEM_PROMPT


def test_agent_honors_a_pre_cancelled_task():
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(AgentCancelled, match="已停止"):
        AgentRegistry().chat(
            "cancelled", "检查错误", "https://example.test/v1", "key", "model", lambda *_: {},
            cancel_event=cancelled,
        )


def test_agent_executes_tool_and_keeps_context(monkeypatch):
    replies = iter([
        {"choices": [{"message": {"content": "", "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "execute_command", "arguments": '{"command":"uname -s"}'}}]}}]},
        {"choices": [{"message": {"content": "系统是 Linux。"}}]},
        {"choices": [{"message": {"content": "前一步确认系统是 Linux。"}}]},
    ])
    requests = []
    def fake_request(*args, **kwargs):
        requests.append(kwargs["payload"])
        return next(replies)
    monkeypatch.setattr("app.agent.openai_request", fake_request)
    registry = AgentRegistry()
    commands = []
    def execute(command, timeout):
        commands.append((command, timeout))
        return {"exit_code": 0, "stdout": "Linux\n", "stderr": ""}
    first = registry.chat("ssh-1", "查看系统", "https://example.test/v1", "key", "model", execute)
    second = registry.chat("ssh-1", "刚才是什么系统？", "https://example.test/v1", "key", "model", execute)
    assert commands == [("uname -s", 120)]
    assert first["message"] == "系统是 Linux。"
    assert second["message"] == "前一步确认系统是 Linux。"
    assert any(message.get("content") == "系统是 Linux。" for message in requests[-1]["messages"])


def test_agent_emits_live_command_events(monkeypatch):
    replies = iter([
        {"choices": [{"message": {"content": "", "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "execute_command", "arguments": '{"command":"whoami"}'}}]}}]},
        {"choices": [{"message": {"content": "完成"}}]},
    ])
    monkeypatch.setattr("app.agent.openai_request", lambda *args, **kwargs: next(replies))
    events = []
    AgentRegistry().chat(
        "ssh-events", "当前用户", "https://example.test/v1", "key", "model",
        lambda command, timeout: {"exit_code": 0, "stdout": "root\n", "stderr": ""},
        on_event=events.append,
    )
    assert events == [
        {"type": "command_start", "command": "whoami"},
        {"type": "command_end", "exit_code": 0, "stdout": "root\n", "stderr": ""},
    ]


def test_agent_denied_command_is_not_executed(monkeypatch):
    replies = iter([
        {"choices": [{"message": {"content": "", "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "execute_command", "arguments": '{"command":"rm -rf /tmp/demo"}'}}]}}]},
        {"choices": [{"message": {"content": "已取消删除。"}}]},
    ])
    monkeypatch.setattr("app.agent.openai_request", lambda *args, **kwargs: next(replies))
    commands = []
    events = []
    result = AgentRegistry().chat(
        "ssh-denied", "删除目录", "https://example.test/v1", "key", "model",
        lambda command, timeout: commands.append(command) or {},
        command_approver=lambda _command: False,
        on_event=events.append,
    )
    assert commands == []
    assert events == [{"type": "command_denied", "command": "rm -rf /tmp/demo"}]
    assert result["message"] == "已取消删除。"


@pytest.mark.parametrize("command", [
    "rm -rf /var/lib/demo",
    "sudo -n sh -c 'rm -rf /var/lib/demo'",
    "Remove-Item -Recurse -Force C:\\Temp\\demo",
    "cmd.exe /c del /q C:\\Temp\\demo.txt",
    "Format-Volume -DriveLetter D",
    "shutdown.exe /r /t 0",
    "git reset --hard HEAD~1",
    "kubectl delete namespace demo",
    "DROP DATABASE production",
])
def test_dangerous_command_detection_covers_common_operating_systems(command):
    assert classify_dangerous_command(command) is not None


@pytest.mark.parametrize("command", ["ls -la", "Get-ChildItem C:\\Temp", "git status", "systemctl status nginx"])
def test_safe_commands_do_not_require_approval(command):
    assert classify_dangerous_command(command) is None


def test_agent_approval_registry_binds_decision_to_session():
    registry = AgentApprovalRegistry()
    approval_id = registry.create("ssh-1")
    assert not registry.resolve("ssh-2", approval_id, True)
    assert registry.resolve("ssh-1", approval_id, True)
    assert registry.wait(approval_id) is True


def test_agent_approval_registry_keeps_sidebar_and_terminal_scopes_isolated():
    registry = AgentApprovalRegistry()
    sidebar_id = registry.create("ssh-1", "sidebar")
    terminal_id = registry.create("ssh-1", "terminal")
    registry.cancel_session("ssh-1", "sidebar")
    assert registry.wait(sidebar_id) is False
    assert registry.resolve("ssh-1", terminal_id, True)
    assert registry.wait(terminal_id) is True


def test_agent_streams_answer_deltas(monkeypatch):
    chunks = [
        {"choices": [{"delta": {"content": "你好"}}]},
        {"choices": [{"delta": {"content": "，世界"}}]},
    ]
    monkeypatch.setattr("app.agent.openai_stream_request", lambda *_args, **_kwargs: iter(chunks))
    events = []
    message = stream_chat_message("https://example.test/v1", "key", {"model": "model"}, events.append)
    assert message == {"content": "你好，世界"}
    assert events == [
        {"type": "answer_delta", "delta": "你好"},
        {"type": "answer_delta", "delta": "，世界"},
    ]


def test_agent_reports_file_edit_while_streaming_workspace_write_arguments(monkeypatch):
    chunks = [
        {"choices": [{"delta": {"tool_calls": [{
            "index": 0, "id": "write-1", "type": "function",
            "function": {"name": "workspace_", "arguments": ""},
        }]}}]},
        {"choices": [{"delta": {"tool_calls": [{
            "index": 0, "function": {"name": "write", "arguments": '{"path":"index.html","content":"'},
        }]}}]},
        {"choices": [{"delta": {"tool_calls": [{
            "index": 0, "function": {"arguments": "<main>正在生成的较长网页</main>"},
        }]}}]},
        {"choices": [{"delta": {"tool_calls": [{
            "index": 0, "function": {"arguments": '"}'},
        }]}}]},
    ]
    monkeypatch.setattr("app.agent.openai_stream_request", lambda *_args, **_kwargs: iter(chunks))
    events = []

    message = stream_chat_message("https://example.test/v1", "key", {"model": "model"}, events.append)

    assert events == [{"type": "local_tool_prepare", "id": "write-1", "tool": "workspace_write"}]
    assert message["tool_calls"][0]["function"] == {
        "name": "workspace_write",
        "arguments": '{"path":"index.html","content":"<main>正在生成的较长网页</main>"}',
    }


def test_glm_and_deepseek_thinking_is_hidden_but_preserved(monkeypatch):
    chunks = [
        {"choices": [{"delta": {"reasoning_content": "private reasoning"}}]},
        {"choices": [{"delta": {"content": "public answer"}}]},
    ]
    monkeypatch.setattr("app.agent.openai_stream_request", lambda *_args, **_kwargs: iter(chunks))
    events = []
    message = stream_chat_message("https://example.test/v1", "key", {"model": "glm-5"}, events.append)
    assert message == {"content": "public answer", "reasoning_content": "private reasoning"}
    assert events == [
        {"type": "thinking_start"},
        {"type": "thinking_end"},
        {"type": "answer_delta", "delta": "public answer"},
    ]
    assert "private reasoning" not in str(events)
    assert model_request_options("glm-5.2") == {"thinking": {"type": "enabled", "clear_thinking": False}}
    assert model_request_options("deepseek-v4-pro") == {"thinking": {"type": "enabled"}, "reasoning_effort": "high"}
    assert model_request_options("gpt-compatible") == {}


def test_deepseek_tool_round_replays_reasoning_content(monkeypatch):
    replies = iter([
        {"choices": [{"message": {"content": "", "reasoning_content": "hidden plan", "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "execute_command", "arguments": '{"command":"pwd"}'}}]}}]},
        {"choices": [{"message": {"content": "done", "reasoning_content": "hidden summary"}}]},
    ])
    payloads = []
    def fake_request(*_args, **kwargs):
        payloads.append(kwargs["payload"])
        return next(replies)
    monkeypatch.setattr("app.agent.openai_request", fake_request)
    AgentRegistry().chat(
        "ssh-deepseek", "where", "https://example.test/v1", "key", "deepseek-v4-pro",
        lambda *_args: {"exit_code": 0, "stdout": "/root\n", "stderr": ""},
    )
    assert payloads[0]["thinking"] == {"type": "enabled"}
    assert payloads[0]["reasoning_effort"] == "high"
    assert "tool_choice" not in payloads[0]
    assistant = next(message for message in payloads[1]["messages"] if message.get("tool_calls"))
    assert assistant["reasoning_content"] == "hidden plan"


def test_openai_stream_stops_on_finish_reason_without_done(monkeypatch):
    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def __iter__(self):
            return iter([
                b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}\n',
                b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n',
                b'data: {"choices":[{"delta":{"content":"must not be read"}}]}\n',
            ])
    monkeypatch.setattr("app.agent.urllib.request.urlopen", lambda *_args, **_kwargs: FakeResponse())
    events = list(openai_stream_request("https://example.test/v1", "key", "chat/completions", {"model": "model"}))
    assert len(events) == 2
    assert events[-1]["choices"][0]["finish_reason"] == "stop"


def test_agent_can_search_without_executing_ssh_command(monkeypatch):
    replies = iter([
        {"choices": [{"message": {"content": "", "tool_calls": [{"id": "search-1", "type": "function", "function": {"name": "web_search", "arguments": '{"query":"Python latest release","max_results":3}'}}]}}]},
        {"choices": [{"message": {"content": "搜索结果显示有新版。来源：https://python.org/"}}]},
    ])
    monkeypatch.setattr("app.agent.openai_request", lambda *args, **kwargs: next(replies))
    monkeypatch.setattr("app.agent.web_search", lambda query, count: [{"title": "Python", "url": "https://python.org/", "snippet": "Latest release"}])
    executed = []
    result = AgentRegistry().chat("ssh-2", "Python 最新版是什么？", "https://example.test/v1", "key", "model", lambda command, timeout: executed.append(command))
    assert executed == []
    assert result["steps"] == [{"search": "Python latest release", "result_count": 1}]
    assert result["message"].startswith("搜索结果")


def test_agent_prompt_includes_local_date_and_workspace_tools(monkeypatch):
    replies = iter([
        {"choices": [{"message": {"content": "", "tool_calls": [{
            "id": "local-1", "type": "function",
            "function": {"name": "workspace_list", "arguments": '{"path":"."}'},
        }]}}]},
        {"choices": [{"message": {"content": "已查看 workspace。"}}]},
    ])
    payloads = []
    local_calls = []
    events = []

    def fake_request(*_args, **kwargs):
        payloads.append(kwargs["payload"])
        return next(replies)

    def local_executor(tool, arguments):
        local_calls.append((tool, arguments))
        return {"path": ".", "entries": [], "truncated": False}

    monkeypatch.setattr("app.agent.openai_request", fake_request)
    result = AgentRegistry().chat(
        "ssh-local", "查看本地工作区", "https://example.test/v1", "key", "model", lambda *_args: {},
        local_executor=local_executor, on_event=events.append,
    )
    names = {tool["function"]["name"] for tool in payloads[0]["tools"]}
    assert names >= {
        "workspace_list", "workspace_read", "workspace_write", "sftp_transfer",
        "workspace_root_list", "workspace_root_read", "workspace_root_write", "workspace_root_sftp_transfer",
    }
    assert "只有当用户在当前消息中明确要求访问共享 workspace 根目录" in payloads[0]["messages"][0]["content"]
    assert datetime.now().astimezone().date().isoformat() in payloads[0]["messages"][0]["content"]
    assert local_calls == [("workspace_list", {"path": "."})]
    assert result["steps"] == [{"tool": "workspace_list", "path": "."}]
    assert events == [
        {"type": "local_tool_start", "id": "local-1", "tool": "workspace_list", "label": ".", "direction": None},
        {"type": "local_tool_end", "id": "local-1", "tool": "workspace_list", "label": ".", "direction": None, "success": True, "size": None, "entry_count": 0},
    ]


def test_web_search_uses_bundled_searxng_json(monkeypatch):
    payload = {
        "results": [
            {"title": "Example <b>Result</b>", "url": "https://example.com/page", "content": "Useful &amp; current <b>summary</b>."},
            {"title": "Unsafe", "url": "javascript:alert(1)", "content": "ignored"},
        ]
    }
    captured = {}

    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self, _limit): return json.dumps(payload).encode()

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setenv("WEBSSH_SEARXNG_URL", "http://127.0.0.1:9876")
    monkeypatch.setattr("app.agent.urllib.request.urlopen", fake_urlopen)
    assert web_search("release notes", 5) == [{
        "title": "Example Result",
        "url": "https://example.com/page",
        "snippet": "Useful & current summary.",
    }]
    assert captured["timeout"] == 20
    assert captured["url"].startswith("http://127.0.0.1:9876/search?")
    assert "format=json" in captured["url"] and "q=release+notes" in captured["url"]


def test_web_page_parser_extracts_readable_text_and_clickable_links():
    parser = _WebPageParser("https://example.test/docs/start")
    parser.feed("""<html><head><title>Deploy Guide</title><style>hidden</style></head>
        <body><h1>Install</h1><p>Run the setup command.</p>
        <a href="../config">Configuration</a><script>ignore()</script></body></html>""")
    title, text, links = parser.result()
    assert title == "Deploy Guide"
    assert "Run the setup command." in text
    assert "hidden" not in text and "ignore()" not in text
    assert links == [{"text": "Configuration", "url": "https://example.test/config"}]


def test_web_fetch_url_rejects_private_network(monkeypatch):
    monkeypatch.setattr("app.agent.socket.getaddrinfo", lambda *_args, **_kwargs: [(2, 1, 6, "", ("127.0.0.1", 80))])
    with pytest.raises(AgentError, match="内网"):
        _validate_public_url("http://example.test/admin")


def test_web_fetch_allows_detected_tun_fake_ip_but_not_literal_ip(monkeypatch):
    def fake_getaddrinfo(host, port, **_kwargs):
        if host == "internal.test":
            return [(2, 1, 6, "", ("10.0.0.8", port))]
        offset = 5 if host == "example.com" else 6 if host == "www.iana.org" else 7
        return [
            (2, 1, 6, "", (f"198.18.0.{offset}", port)),
            (23, 1, 6, "", (f"fc00::{offset}", port, 0, 0)),
        ]

    monkeypatch.setattr("app.agent.socket.getaddrinfo", fake_getaddrinfo)
    assert _validate_public_url("https://www.reuters.com/world/us") == "https://www.reuters.com/world/us"
    with pytest.raises(AgentError, match="保留地址"):
        _validate_public_url("https://198.18.0.7/")
    with pytest.raises(AgentError, match="内网"):
        _validate_public_url("https://internal.test/")


def test_bundled_searxng_settings_are_private_and_enable_json(tmp_path):
    path = tmp_path / "settings.yml"
    _write_settings(path, 9123)
    value = path.read_text(encoding="utf-8")
    assert 'bind_address: "127.0.0.1"' in value
    assert "port: 9123" in value
    assert "    - json" in value
    assert "public_instance: false" in value
    assert "brave" in value and "startpage" in value and "duckduckgo" in value


def test_agent_can_fetch_page_then_execute_deployment(monkeypatch):
    replies = iter([
        {"choices": [{"message": {"content": "", "tool_calls": [{"id": "fetch-1", "type": "function", "function": {"name": "web_fetch", "arguments": '{"url":"https://github.com/acme/demo"}'}}]}}]},
        {"choices": [{"message": {"content": "", "tool_calls": [{"id": "exec-1", "type": "function", "function": {"name": "execute_command", "arguments": '{"command":"git clone https://github.com/acme/demo.git"}'}}]}}]},
        {"choices": [{"message": {"content": "部署完成。"}}]},
    ])
    payloads = []
    def fake_request(*args, **kwargs):
        payloads.append(kwargs["payload"])
        return next(replies)
    monkeypatch.setattr("app.agent.openai_request", fake_request)
    monkeypatch.setattr("app.agent.web_fetch", lambda url: {
        "url": url, "title": "acme/demo", "content_type": "text/html", "text": "Install with git clone",
        "links": [{"text": "README", "url": url + "/blob/main/README.md"}], "truncated": False,
    })
    commands = []
    result = AgentRegistry().chat(
        "ssh-fetch", "部署这个项目 https://github.com/acme/demo", "https://example.test/v1", "key", "model",
        lambda command, timeout: commands.append((command, timeout)) or {"exit_code": 0, "stdout": "ok", "stderr": ""},
    )
    assert {tool["function"]["name"] for tool in payloads[0]["tools"]} >= {"web_search", "web_fetch", "execute_command"}
    assert commands == [("git clone https://github.com/acme/demo.git", 120)]
    assert result["steps"][0] == {"fetch": "https://github.com/acme/demo", "title": "acme/demo", "link_count": 1, "truncated": False}
    assert result["message"] == "部署完成。"


def test_agent_can_disable_builtin_search_and_use_mcp(monkeypatch):
    replies = iter([
        {"choices": [{"message": {"content": "", "tool_calls": [{"id": "mcp-1", "type": "function", "function": {"name": "mcp_7_web_search", "arguments": '{"query":"release"}'}}]}}]},
        {"choices": [{"message": {"content": "MCP 返回了结果。"}}]},
    ])
    payloads = []
    def fake_request(*args, **kwargs):
        payloads.append(kwargs["payload"])
        return next(replies)
    monkeypatch.setattr("app.agent.openai_request", fake_request)
    calls = []
    result = AgentRegistry().chat(
        "ssh-3", "查一下版本", "https://example.test/v1", "key", "model", lambda *_: {},
        builtin_web_search=False,
        mcp_tools=[{"server_id": 7, "server_name": "Search", "tool_name": "web_search", "exposed_name": "mcp_7_web_search", "description": "search", "input_schema": {"type": "object"}}],
        mcp_executor=lambda server_id, tool, arguments: calls.append((server_id, tool, arguments)) or {"content": [{"type": "text", "text": "ok"}]},
    )
    names = [tool["function"]["name"] for tool in payloads[0]["tools"]]
    assert "web_search" not in names
    assert "mcp_7_web_search" in names
    assert calls == [(7, "web_search", {"query": "release"})]
    assert result["steps"] == [{"mcp": "Search", "tool": "web_search"}]


def test_mcp_url_and_search_tool_filter():
    assert normalize_mcp_url("https://example.test/mcp") == "https://example.test/mcp"
    tools = [{"name": "web_search", "description": "Find pages"}, {"name": "calculator", "description": "Math"}]
    assert search_tools(tools) == [tools[0]]


def test_database_additive_columns_exist(tmp_path):
    db = Database(tmp_path / "new.db")
    server_columns = {row["name"] for row in db.fetchall("PRAGMA table_info(servers)")}
    agent_columns = {row["name"] for row in db.fetchall("PRAGMA table_info(agent_settings)")}
    assert "ssh_key_id" in server_columns
    assert "os_type" in server_columns
    assert "builtin_web_search" in agent_columns
    db.close()


@pytest.mark.parametrize(("release", "expected"), [
    ('NAME="Ubuntu"\nID=ubuntu\nID_LIKE=debian\n', "ubuntu"),
    ('PRETTY_NAME="Debian GNU/Linux"\nID=debian\n', "debian"),
    ('NAME="Rocky Linux"\nID=rocky\nID_LIKE="rhel centos fedora"\n', "rocky"),
    ('NAME="Alpine Linux"\nID=alpine\n', "alpine"),
    ('ID=unknown\nID_LIKE=linux\n', "linux"),
    ('ID=Darwin\n', "macos"),
    ('Microsoft Windows [Version 10.0.20348.2402]', "windows"),
    ('', "default"),
])
def test_remote_os_release_is_normalized_for_host_icons(release, expected):
    assert normalize_remote_os(release) == expected


def test_accepted_host_key_is_trusted_for_connection_retry(tmp_path):
    import paramiko
    key = paramiko.RSAKey.generate(1024)
    other_key = paramiko.RSAKey.generate(1024)
    db = Database(tmp_path / "host-key.db")
    added = []
    client = type("Client", (), {"get_host_keys": lambda self: type("Keys", (), {"add": lambda self, *args: added.append(args)})()})()
    policy = VerifiedHostKeyPolicy(db, "example.test", 22, key)
    policy.missing_host_key(client, "example.test", key)
    assert added and added[0][0] == "example.test"
    with pytest.raises(HostKeyRequired) as changed:
        policy.missing_host_key(client, "example.test", other_key)
    assert changed.value.changed is True
    db.close()


def test_server_address_change_resets_cached_os_icon(monkeypatch, tmp_path):
    database = Database(tmp_path / "servers.db")
    test_vault = Vault(database)
    server_id = database.execute(
        "INSERT INTO servers(name,host,port,username,os_type) VALUES(?,?,?,?,?)",
        ("prod", "old.example.test", 22, "root", "ubuntu"),
    ).lastrowid
    monkeypatch.setattr(main_app, "db", database)
    monkeypatch.setattr(main_app, "vault", test_vault)

    unchanged = ServerBody(name="renamed", host="old.example.test", port=22, username="root", note="updated")
    main_app.update_server(server_id, unchanged)
    assert database.fetchone("SELECT os_type FROM servers WHERE id=?", (server_id,))["os_type"] == "ubuntu"

    access_changed = ServerBody(name="renamed", host="old.example.test", port=22, username="admin", note="updated")
    assert main_app.update_server(server_id, access_changed)["os_type"] == "default"

    database.execute("UPDATE servers SET os_type='ubuntu' WHERE id=?", (server_id,))
    moved = ServerBody(name="renamed", host="new.example.test", port=2222, username="admin", note="updated")
    result = main_app.update_server(server_id, moved)
    assert result["os_type"] == "default"
    assert database.fetchone("SELECT os_type FROM servers WHERE id=?", (server_id,))["os_type"] == "default"
    database.close()


def test_changed_host_key_can_be_retrusted_and_forces_os_redetection(monkeypatch, tmp_path):
    import paramiko

    database = Database(tmp_path / "changed-host-key.db")
    server_id = database.execute(
        "INSERT INTO servers(name,host,port,username,os_type) VALUES(?,?,?,?,?)",
        ("prod", "example.test", 22, "root", "ubuntu"),
    ).lastrowid
    changed_key = paramiko.RSAKey.generate(1024)

    class FakeChannel:
        closed = True

        def settimeout(self, _timeout):
            pass

    session = SimpleNamespace(id="session-1", channel=FakeChannel())

    class FakeSessions:
        def __init__(self):
            self.attempts = 0

        def connect(self, data):
            self.attempts += 1
            if self.attempts == 1:
                raise HostKeyRequired("example.test", 22, changed_key, changed=True)
            assert data["trusted_host_key"] is changed_key
            return session

        def close(self, _session_id):
            pass

    class FakeWebSocket:
        headers = {}

        def __init__(self):
            self.received = iter([
                {"type": "connect", "server_id": server_id, "cols": 100, "rows": 30},
                {"type": "trust_host", "accept": True},
                {"type": "close"},
            ])
            self.sent = []

        async def accept(self):
            pass

        async def receive_json(self):
            return next(self.received)

        async def send_json(self, message):
            self.sent.append(message)

        async def close(self, code=None):
            pass

    fake_sessions = FakeSessions()
    websocket = FakeWebSocket()
    monkeypatch.setattr(main_app, "db", database)
    monkeypatch.setattr(main_app, "vault", Vault(database))
    monkeypatch.setattr(main_app, "sessions", fake_sessions)
    monkeypatch.setattr(main_app, "detect_remote_os", lambda _session: "debian")

    asyncio.run(main_app.terminal_socket(websocket))

    assert fake_sessions.attempts == 2
    assert any(message["type"] == "host_key" and message["changed"] for message in websocket.sent)
    assert any(message["type"] == "connected" for message in websocket.sent)
    assert database.fetchone("SELECT key_base64 FROM host_keys WHERE host=? AND port=?", ("example.test", 22))["key_base64"] == changed_key.get_base64()
    assert database.fetchone("SELECT os_type FROM servers WHERE id=?", (server_id,))["os_type"] == "debian"
    database.close()
