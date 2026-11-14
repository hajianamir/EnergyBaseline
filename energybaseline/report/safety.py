"""Fail closed on connection material and unsafe spreadsheet strings in exports."""

from __future__ import annotations
import re
from ..monitoring.safety import assert_safe_payload


def assert_safe(value):
    assert_safe_payload(value)
    if isinstance(value, dict):
        for key, item in value.items():
            if re.search(r"(?:^|_)(?:db_url|dsn|token|private_key)(?:$|_)", str(key), re.I):
                raise ValueError("Connection material is forbidden in report state")
            assert_safe(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            assert_safe(item)
    elif isinstance(value, str):
        if re.search(r"(?i)(?:file|https?|ftp|javascript)://|<\s*(?:script|iframe|object)\b", value):
            raise ValueError("Active or remote content is forbidden in report state")


def excel_literal(cell, value):
    cell.value = value
    if isinstance(value, str):
        cell.data_type = "s"  # Never execute imported table cells as spreadsheet formulas.
