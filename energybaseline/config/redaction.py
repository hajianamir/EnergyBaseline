"""Safe connection diagnostics. Never return unparsed connection material."""

from __future__ import annotations

import re
from urllib.parse import urlsplit


def safe_database_label(value: str) -> str:
    """Keep the driver identity only, never credentials or opaque DSNs.

    ODBC and malformed/non-URL connection strings are deliberately opaque. Query
    values are not needed to identify an endpoint and are all redacted, including
    unknown vendor-specific secret keys and encoded nested connection strings.
    """
    if not isinstance(value, str) or not value:
        return "[unconfigured database]"
    try:
        parts = urlsplit(value)
        if not parts.scheme or "://" not in value or any(ord(c) < 32 for c in value):
            return "[redacted connection string]"
        driver = parts.scheme.split("+", 1)[0]
        if driver not in {"postgresql", "postgres", "mysql", "mariadb", "mssql", "sqlite", "oracle", "odbc"}:
            return "[redacted connection string]"
        # Endpoint names, option names and paths can themselves carry opaque
        # credentials. Diagnostics disclose the driver only, never URL components.
        return driver + "://***"
    except (ValueError, TypeError, UnicodeError):
        return "[redacted connection string]"


def safe_database_error(exc: BaseException) -> str:
    """Driver exception messages may contain a DSN, parameters or credentials."""
    name = type(exc).__name__
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        name = "DatabaseError"
    return f"{name}: database operation failed; check the endpoint, driver, access policy and credentials."
