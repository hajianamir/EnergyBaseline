"""Reject connection material before monitoring JSON/audit persistence."""

from __future__ import annotations
import re
import zipfile

SENSITIVE = re.compile(r"password|passwd|secret|api.?key|credential|connection.?string|odbc", re.I)
USERINFO = re.compile(r"[a-z][a-z0-9+.-]*://[^\s/]+@|(?:PWD|PASSWORD)\s*=", re.I)


def assert_safe_payload(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if SENSITIVE.search(str(key)):
                raise ValueError("Connection material is forbidden in monitoring exports")
            assert_safe_payload(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            assert_safe_payload(item)
    elif isinstance(value, str) and (
        USERINFO.search(value) or re.search(r"(?:password|api_key|secret)\s*[:=]", value, re.I)
    ):
        raise ValueError("Connection material is forbidden in monitoring exports")


def inspect_export_zip(path):
    """Read-only inspection hook for later-phase project packaging; never extracts."""
    import json

    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.file_size > 20_000_000:
                raise ValueError("Export entry exceeds inspection budget")
            if info.is_dir():
                continue
            raw = archive.read(info)
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            assert_safe_payload(text)
            if info.filename.endswith(".json"):
                assert_safe_payload(json.loads(text))
    return True
