from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PasswordBody(BaseModel):
    password: str = Field(min_length=8, max_length=512)


class ServerBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=255)
    auth_type: Literal["password", "private_key"] = "password"
    password: str | None = None
    private_key: str | None = None
    passphrase: str | None = None
    ssh_key_id: int | None = Field(default=None, ge=1)
    note: str = Field(default="", max_length=1000)


class ShortcutBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    command: str = Field(min_length=1, max_length=100000)
    group_name: str = Field(default="", max_length=100)
    sort_order: int = 0


class TabBody(BaseModel):
    id: str = Field(min_length=8, max_length=100)
    title: str = Field(min_length=1, max_length=100)
    server_id: int | None = None
    position: int = 0
    last_path: str = "."


class PathBody(BaseModel):
    session_id: str
    path: str


class TransferBody(BaseModel):
    session_id: str
    source: str
    destination: str
    overwrite: bool = False


class TrustBody(BaseModel):
    host: str
    port: int
    algorithm: str
    fingerprint: str
    key_base64: str


class UploadInitBody(BaseModel):
    session_id: str
    path: str
    filename: str = Field(min_length=1, max_length=1024)
    size: int = Field(ge=0)
    overwrite: bool = False


class EditorSaveBody(BaseModel):
    session_id: str
    path: str
    content: str = Field(max_length=6 * 1024 * 1024)
    expected_mtime: int | None = None
    force: bool = False


class AgentSettingsBody(BaseModel):
    api_url: str = Field(min_length=1, max_length=2000)
    api_key: str | None = Field(default=None, max_length=4000)
    model: str = Field(default="", max_length=500)
    builtin_web_search: bool = True


class SSHKeyBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    private_key: str = Field(min_length=32, max_length=200000)
    passphrase: str | None = Field(default=None, max_length=4000)


class MCPServerBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=8, max_length=2000)
    auth_token: str | None = Field(default=None, max_length=8000)


class MCPEnabledBody(BaseModel):
    enabled: bool


class AgentModelsBody(BaseModel):
    api_url: str = Field(min_length=1, max_length=2000)
    api_key: str | None = Field(default=None, max_length=4000)


class AgentChatBody(BaseModel):
    session_id: str
    message: str = Field(min_length=1, max_length=20000)
