import sqlite3
import sys

import pytest

from app.database import Database
from app.ssh import HostKeyRequired, SSHSession, SessionRegistry, UploadRegistry, VerifiedHostKeyPolicy, clean_remote_path
from app.vault import Vault, VaultError
from app.agent import AgentError, AgentRegistry, _WebPageParser, _validate_public_url, list_models, normalize_api_base, openai_url
from app.mcp import normalize_mcp_url, search_tools
from app.device_secrets import protect as protect_device_secret, unprotect as unprotect_device_secret


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
    assert "builtin_web_search" in agent_columns
    db.close()


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
