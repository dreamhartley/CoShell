from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class MCPError(ValueError):
    pass


def normalize_mcp_url(value: str) -> str:
    value = value.strip()
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise MCPError("MCP 地址必须是有效的 http:// 或 https:// 地址")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


def _decode_response(content_type: str, raw: bytes) -> dict[str, Any]:
    text = raw.decode("utf-8", "replace")
    if "text/event-stream" in content_type:
        payloads = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
        text = next((item for item in reversed(payloads) if item and item != "[DONE]"), "")
    try:
        result = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MCPError("MCP 服务返回了无效响应") from exc
    if not isinstance(result, dict):
        raise MCPError("MCP 服务返回格式不正确")
    if result.get("error"):
        error = result["error"]
        raise MCPError(str(error.get("message") if isinstance(error, dict) else error))
    return result


def _rpc(url: str, token: str, method: str, params: dict[str, Any], request_id: int, session_id: str = "") -> tuple[dict[str, Any], str]:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "User-Agent": "SSH-Terminal-MCP/1.0",
    }
    if token:
        headers["Authorization"] = token if token.lower().startswith(("bearer ", "basic ")) else f"Bearer {token}"
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    payload = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}, ensure_ascii=False).encode()
    request = urllib.request.Request(normalize_mcp_url(url), data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            result = _decode_response(response.headers.get("Content-Type", ""), response.read(4 * 1024 * 1024))
            return result.get("result") or {}, response.headers.get("Mcp-Session-Id", session_id)
    except urllib.error.HTTPError as exc:
        detail = exc.read(2000).decode("utf-8", "replace")
        raise MCPError(f"MCP 请求失败（HTTP {exc.code}）：{detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise MCPError(f"无法连接 MCP 服务：{exc}") from exc


def _session(url: str, token: str) -> str:
    _, session_id = _rpc(url, token, "initialize", {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "ssh-terminal", "version": "1.0"},
    }, 1)
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = token if token.lower().startswith(("bearer ", "basic ")) else f"Bearer {token}"
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    notification = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(normalize_mcp_url(url), data=notification, headers=headers, method="POST"), timeout=10):
            pass
    except urllib.error.HTTPError as exc:
        if exc.code not in (202, 204):
            raise MCPError(f"MCP 初始化确认失败（HTTP {exc.code}）") from exc
    return session_id


def list_tools(url: str, token: str = "") -> list[dict[str, Any]]:
    session_id = _session(url, token)
    result, _ = _rpc(url, token, "tools/list", {}, 2, session_id)
    tools = result.get("tools") or []
    return [tool for tool in tools if isinstance(tool, dict) and tool.get("name")]


def call_tool(url: str, token: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    session_id = _session(url, token)
    result, _ = _rpc(url, token, "tools/call", {"name": name, "arguments": arguments}, 2, session_id)
    return result


def search_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expose only tools that clearly advertise search to the terminal Agent."""
    return [tool for tool in tools if "search" in f"{tool.get('name', '')} {tool.get('description', '')}".lower()]
