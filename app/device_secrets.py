"""Windows user-and-device-bound secret storage using DPAPI."""

from __future__ import annotations

import ctypes
import hashlib
import sys
from ctypes import wintypes


class DeviceSecretError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_byte))]


def _blob(value: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(value)
    return _DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _device_entropy() -> bytes:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
            machine_guid = str(winreg.QueryValueEx(key, "MachineGuid")[0])
    except OSError as exc:
        raise DeviceSecretError("无法读取 Windows 设备标识") from exc
    return hashlib.sha256(("LightSSHTerminal\0" + machine_guid).encode()).digest()


def _crypt(value: bytes, protect: bool) -> bytes:
    if sys.platform != "win32":
        raise DeviceSecretError("自动解锁目前仅支持 Windows 桌面版")

    source, source_buffer = _blob(value)
    entropy, entropy_buffer = _blob(_device_entropy())
    result = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN

    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(source), "LightSSHTerminal", ctypes.byref(entropy),
            None, None, flags, ctypes.byref(result),
        )
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(source), None, ctypes.byref(entropy),
            None, None, flags, ctypes.byref(result),
        )
    # Keep ctypes-owned buffers alive through the native call.
    del source_buffer, entropy_buffer
    if not ok:
        raise DeviceSecretError("设备凭据不可用，可能已更换电脑或 Windows 用户")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel32.LocalFree(result.data)


def protect(value: str) -> bytes:
    if not value:
        raise DeviceSecretError("不能保存空密码")
    return _crypt(value.encode("utf-8"), True)


def unprotect(value: bytes) -> str:
    try:
        return _crypt(value, False).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DeviceSecretError("设备凭据内容无效") from exc
