"""Verified OS protection or session-only credentials; disk key files require explicit development opt-in."""
from __future__ import annotations

import base64
import json
import logging
import os
import sys
import stat
import threading

from ..config.settings import get_paths, insecure_credential_file_allowed

log = logging.getLogger("energybaseline.secrets")


class CredentialStorageUnavailable(RuntimeError):
    """Credentials cannot be recovered using the available protection backend."""


_backend_lock = threading.RLock()
_session_key: bytes | None = None
_backend = {"name": "session-only (OS secure storage not yet verified)", "persistent": False, "secure_store": False}


def backend_status() -> dict:
    with _backend_lock:
        return dict(_backend)


def backend_name() -> str:
    return backend_status()["name"]


def _record_backend(name: str, *, persistent: bool, secure_store: bool) -> None:
    updated = {"name": name, "persistent": persistent, "secure_store": secure_store}
    if updated != _backend:
        _backend.update(updated)
        level = logging.INFO if secure_store else logging.WARNING
        log.log(level, "credentials.backend_changed backend=%s persistent=%s secure_store=%s", name, persistent, secure_store)


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
def _file_key(*, create: bool) -> bytes | None:
    path = get_paths()["data"] / ".eb_master.key"
    if not path.exists() and not path.is_symlink():
        if not create:
            return None
        from cryptography.fernet import Fernet
        path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags, 0o600)
        except FileExistsError:
            return _file_key(create=False)
        with os.fdopen(fd, "wb") as stream:
            stream.write(key)
        return key
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or (os.name == "posix" and (st.st_mode & 0o077 or st.st_uid != os.getuid())):
        raise CredentialStorageUnavailable("The legacy credential key must be a private regular file owned by this user.")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        return stream.read().strip()


def _secure_keyring():
    """Select only supported OS stores, never a third-party plaintext backend."""
    import keyring
    backend = keyring.get_keyring()
    candidates = getattr(backend, "backends", [backend])
    approved = {
        ("keyring.backends.SecretService", "Keyring"),
        ("keyring.backends.Windows", "WinVaultKeyring"),
        ("keyring.backends.macOS", "Keyring"),
    }
    for candidate in candidates:
        cls = type(candidate)
        if (cls.__module__, cls.__name__) in approved:
            return candidate
    raise CredentialStorageUnavailable("No supported OS credential backend is active.")


def _fernet(*, create: bool):
    from cryptography.fernet import Fernet
    global _session_key
    with _backend_lock:
        key = None
        try:
            keyring = _secure_keyring()
            key = keyring.get_password("EnergyBaseline", "master-key")
            if not key and create:
                key = Fernet.generate_key().decode("ascii")
                keyring.set_password("EnergyBaseline", "master-key", key)
                if keyring.get_password("EnergyBaseline", "master-key") != key:
                    raise CredentialStorageUnavailable("OS credential store did not confirm the saved key.")
            if key:
                cipher = Fernet(key.encode("ascii"))
                _record_backend("os-keyring+fernet", persistent=True, secure_store=True)
                return cipher, "fernet"
        except (ImportError, RuntimeError, OSError, ValueError):
            # Backend failures never include provider exception text (it may contain secrets).
            log.debug("credentials.os_store_unavailable")
        except Exception:
            log.debug("credentials.os_store_provider_failed")
        if not create or insecure_credential_file_allowed():
            key = _file_key(create=create)
            if key:
                _record_backend("legacy-keyfile (unprotected key; development opt-in required for writes)", persistent=True, secure_store=False)
                return Fernet(key), "fernet"
        if not create:
            raise CredentialStorageUnavailable("The original credential key is unavailable. Re-enter credentials; no replacement key was created.")
        if _session_key is None:
            _session_key = Fernet.generate_key()
        _record_backend("session-only (credentials expire when the application closes)", persistent=False, secure_store=False)
        return Fernet(_session_key), "session-fernet"


def encrypt_text(plain: str) -> str:
    raw = plain.encode("utf-8")
    if sys.platform == "win32":
        protected = _dpapi(raw, True)
        with _backend_lock:
            _record_backend("windows-dpapi", persistent=True, secure_store=True)
        return "dpapi:" + base64.b64encode(protected).decode("ascii")
    cipher, scheme = _fernet(create=True)
    return scheme + ":" + cipher.encrypt(raw).decode("ascii")


def decrypt_text(token: str) -> str:
    if not token:
        return ""
    scheme, _, body = token.partition(":")
    if scheme == "dpapi":
        return _dpapi(base64.b64decode(body), False).decode("utf-8")
    if scheme == "session-fernet":
        from cryptography.fernet import Fernet
        if _session_key is None:
            raise CredentialStorageUnavailable("Session credentials have expired. Re-enter the password; no key is stored on disk.")
        return Fernet(_session_key).decrypt(body.encode("ascii")).decode("utf-8")
    if scheme == "fernet":
        from cryptography.fernet import Fernet, InvalidToken
        cipher, _ = _fernet(create=False)
        try:
            return cipher.decrypt(body.encode("ascii")).decode("utf-8")
        except InvalidToken:
            # A pre-existing file key may predate a newly available OS store.
            # Reading never creates/replaces keys or silently migrates credentials.
            legacy = _file_key(create=False)
            if legacy is None:
                raise CredentialStorageUnavailable("The original credential key is unavailable; re-enter credentials.") from None
            return Fernet(legacy).decrypt(body.encode("ascii")).decode("utf-8")
    raise ValueError("Unknown secret scheme")


def encrypt_json(obj: dict) -> str:
    return encrypt_text(json.dumps(obj))


def decrypt_json(token: str) -> dict:
    return json.loads(decrypt_text(token)) if token else {}
