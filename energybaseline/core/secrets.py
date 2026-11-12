"""Encryption for stored credentials: Windows DPAPI -> OS keyring -> Fernet key-file (dev only)."""
from __future__ import annotations

import base64
import json
import logging
import os
import sys

from ..config import ensure_dirs

log = logging.getLogger("energybaseline.secrets")


def backend_name() -> str:
    if sys.platform == "win32":
        return "windows-dpapi"
    try:
        import keyring  # noqa: F401
        return "os-keyring+fernet"
    except Exception:
        return "fernet-keyfile (non-Windows development fallback)"


# ---------------------------------------------------------------- DPAPI
def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    inb = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    outb = BLOB()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData  # type: ignore[attr-defined]
    if not fn(ctypes.byref(inb), None, None, None, None, 0, ctypes.byref(outb)):
        raise OSError("DPAPI call failed")
    try:
        return ctypes.string_at(outb.pbData, outb.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(outb.pbData)  # type: ignore[attr-defined]


# ---------------------------------------------------------------- Fernet
def _fernet():
    from cryptography.fernet import Fernet

    key = None
    try:
        import keyring
        key = keyring.get_password("EnergyBaseline", "master-key")
        if not key:
            key = Fernet.generate_key().decode()
            keyring.set_password("EnergyBaseline", "master-key", key)
    except Exception:
        key = None
    if not key:
        path = ensure_dirs()["data"] / ".eb_master.key"
        if path.exists():
            key = path.read_text().strip()
        else:
            key = Fernet.generate_key().decode()
            path.write_text(key)
            try:
                os.chmod(path, 0o600)
            except Exception:
                pass
    return Fernet(key.encode())


def encrypt_text(plain: str) -> str:
    raw = plain.encode("utf-8")
    if sys.platform == "win32":
        return "dpapi:" + base64.b64encode(_dpapi(raw, True)).decode()
    return "fernet:" + _fernet().encrypt(raw).decode()


def decrypt_text(token: str) -> str:
    if not token:
        return ""
    scheme, _, body = token.partition(":")
    if scheme == "dpapi":
        return _dpapi(base64.b64decode(body), False).decode("utf-8")
    if scheme == "fernet":
        return _fernet().decrypt(body.encode()).decode("utf-8")
    raise ValueError("Unknown secret scheme")


def encrypt_json(obj: dict) -> str:
    return encrypt_text(json.dumps(obj))


def decrypt_json(token: str) -> dict:
    return json.loads(decrypt_text(token)) if token else {}
