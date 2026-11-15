"""Read-only external SQL sources validated via sqlglot AST and driver read-only mode (AC-06, DAT-7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from collections.abc import Mapping
import json
import logging
from pathlib import Path
import re
import sqlite3
import ssl
from urllib.parse import quote_plus

import pandas as pd
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool
import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from ..config.redaction import safe_database_error
from ..db import models as M
from ..db.session import session_scope
from .secrets import decrypt_json, encrypt_json, backend_status

log = logging.getLogger("energybaseline.sql")
DRIVERS = ["SQLite", "MySQL", "SQL Server", "PostgreSQL", "ODBC"]
DEFAULT_PORT = {"MySQL": 3306, "SQL Server": 1433, "PostgreSQL": 5432, "SQLite": None, "ODBC": None}
DEFAULT_QUERY_TIMEOUT_SECONDS = 10
DEFAULT_MAX_ROWS = 500_000


class SourceError(RuntimeError):
    """Raised on disallowed SQL constructs, read-only violations, or unreachable external sources."""


@dataclass
class ConnectionProfile:
    name: str
    driver: str = "SQLite"
    host: str = ""
    port: int | None = None
    database: str = ""
    username: str = ""
    password: str = field(default="", repr=False)
    ssl: bool = False
    ssl_ca: str = ""
    odbc_driver: str = "ODBC Driver 18 for SQL Server"
    odbc_string: str = field(default="", repr=False)
    queries: dict = field(default_factory=dict, repr=False)  # template name -> SQL


_DRIVER_TO_DIALECT: dict[str, str | None] = {
    "SQLite": "sqlite",
    "MySQL": "mysql",
    "PostgreSQL": "postgres",
    "SQL Server": "tsql",
    "ODBC": None,
}

_ALLOWED_ROOT_EXPS = (exp.Select, exp.Union, exp.Intersect, exp.Except)

_FORBIDDEN_AST_EXPS = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Command,
    exp.Copy,
    exp.Pragma,
    exp.Into,
    exp.Lock,
    exp.Set,
    exp.Grant,
    exp.Revoke,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Use,
    exp.LoadData,
)

_FORBIDDEN_FUNCTIONS = frozenset(
    {
        "xp_cmdshell",
        "sp_oacreate",
        "sp_oamethod",
        "sp_executesql",
        "openrowset",
        "opendatasource",
        "openquery",
        "load_file",
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_execute_server_program",
        "lo_import",
        "lo_export",
        "dblink",
        "dblink_exec",
        "readfile",
        "writefile",
        "load_extension",
    }
)


def _strip_leading_trailing(sql: str) -> str:
    """Trim outer whitespace and a single trailing statement terminator while preserving original SQL."""
    cleaned = sql.strip()
    while cleaned.endswith(";"):
        cleaned = cleaned[:-1].strip()
    return cleaned


def validate_select(sql: str, *, dialect: str | None = None) -> str:
    """Validate via ``sqlglot`` AST that ``sql`` is a single read-only ``SELECT`` / ``WITH`` query (AC-06, DAT-7)."""
    if not isinstance(sql, str) or not sql.strip():
        raise SourceError("The query is empty.")

    stripped = _strip_leading_trailing(sql)
    if not stripped:
        raise SourceError("The query is empty.")

    try:
        statements = [st for st in sqlglot.parse(stripped, read=dialect) if st is not None]
    except SqlglotError:
        raise SourceError("SQL syntax / read-only AST validation failed. Check the query structure; SQL text is omitted from diagnostics.") from None

    if len(statements) == 0:
        raise SourceError("The query contains no executable SQL statement.")
    if len(statements) > 1:
        raise SourceError("Only a single SELECT statement is allowed (multi-statement queries are rejected).")

    root = statements[0]
    if not isinstance(root, _ALLOWED_ROOT_EXPS):
        raise SourceError(
            f"Only read-only SELECT / WITH / UNION queries are allowed (got {type(root).__name__})."
        )

    for node in root.walk():
        if isinstance(node, _FORBIDDEN_AST_EXPS):
            raise SourceError(
                f"Disallowed SQL construct '{type(node).__name__}' in read-only connector."
            )
        if isinstance(node, exp.Anonymous):
            fn_name = str(node.name or "").strip().lower()
            if fn_name in _FORBIDDEN_FUNCTIONS:
                raise SourceError(f"Function '{node.name}' is forbidden in read-only SQL queries.")
        elif isinstance(node, exp.Func):
            for candidate in (node.sql_name().lower(), str(getattr(node, "name", "") or "").lower()):
                if candidate in _FORBIDDEN_FUNCTIONS:
                    raise SourceError(f"Function '{candidate}' is forbidden in read-only SQL queries.")
        elif isinstance(node, exp.Table):
            tbl_name = str(node.name or "").strip().lower()
            if tbl_name in _FORBIDDEN_FUNCTIONS:
                raise SourceError(f"Rowset source '{node.name}' is forbidden in read-only SQL queries.")

    return stripped


_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class QuerySpec:
    """SQL and bound values travel together; editor text never contains values."""

    sql: str
    parameters: tuple[tuple[str, str], ...] = field(default=(), repr=False)

    @property
    def params(self) -> dict[str, str]:
        return dict(self.parameters)


def _quoted_identifier(value: str, dialect: str | None) -> str:
    if not isinstance(value, str) or not value:
        raise SourceError("An identifier must be a nonempty string.")
    components = value.split(".")
    if len(components) > 3 or any(not _IDENT.fullmatch(c) for c in components):
        raise SourceError("Invalid identifier. Use simple schema/table/column names; use the read-only SQL editor for quoted names.")
    return ".".join(exp.to_identifier(c, quoted=True).sql(dialect=dialect) for c in components)


def build_query(
    table: str,
    columns: list[str],
    time_col: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = 100_000,
    *,
    dialect: str | None = None,
) -> QuerySpec:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= DEFAULT_MAX_ROWS:
        raise SourceError(f"limit must be an integer between 1 and {DEFAULT_MAX_ROWS}.")
    if not time_col and (start is not None or end is not None):
        raise SourceError("A time column is required for a date range.")
    params = {}
    dates = {}
    for name, value in (("eb_start", start), ("eb_end", end)):
        if value is not None:
            try:
                if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
                    raise ValueError("Invalid timestamp")
                dates[name] = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            except (ValueError, TypeError) as exc:
                raise SourceError("Date boundaries must be valid ISO-8601 timestamps.") from exc
            params[name] = value.strip()
    if len(dates) == 2:
        try:
            ordered = dates["eb_start"] <= dates["eb_end"]
        except TypeError as exc:
            raise SourceError("Date boundaries must use compatible timezone information.") from exc
        if not ordered:
            raise SourceError("The start boundary must not follow the end boundary.")
    q = f"SELECT {', '.join(_quoted_identifier(c, dialect) for c in columns) if columns else '*'} FROM {_quoted_identifier(table, dialect)}"
    if params:
        tc = _quoted_identifier(time_col, dialect)
        predicates = [f"{tc} >= :eb_start"] if start is not None else []
        predicates += [f"{tc} <= :eb_end"] if end is not None else []
        q += " WHERE " + " AND ".join(predicates)
    if time_col:
        q += " ORDER BY " + _quoted_identifier(time_col, dialect)
    query = sqlglot.parse_one(q, read=dialect).limit(limit)
    # Keep SQLAlchemy's named binding convention across dialect-specific rendering.
    for placeholder in list(query.find_all(exp.Placeholder)):
        placeholder.replace(exp.Var(this=":" + placeholder.name))
    rendered = query.sql(dialect=dialect)
    return QuerySpec(validate_select(rendered, dialect=dialect), tuple(sorted(params.items())))


def _positive_integer(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SourceError(f"{name} must be a positive integer.")
    return value


def _odbc_value(value, *, brace=True):
    """Escape generated attributes; permit plain safe paths for legacy drivers."""
    value = str(value)
    if any(ord(c) < 32 for c in value):
        raise SourceError("ODBC attributes cannot contain control characters.")
    # Some legacy SQLite ODBC drivers interpret braces around Database literally.
    # Only delimiter-free, nonempty, unpadded paths may use that compatibility form.
    if not brace and value and value == value.strip() and not any(c in value for c in ";{}"):
        return value
    return "{" + value.replace("}", "}}") + "}"


def _engine(p: ConnectionProfile, *, timeout_seconds: int = DEFAULT_QUERY_TIMEOUT_SECONDS):
    """Create a read-only database engine with explicit connection/statement timeouts (AC-06, DAT-7)."""
    d = p.driver
    t_sec = _positive_integer(timeout_seconds, "timeout_seconds")
    if d == "SQLite":
        if not p.database:
            raise SourceError("SQLite: please provide the database file path.")
        db_path = Path(p.database)
        if not db_path.is_file():
            raise SourceError(f"SQLite database file '{p.database}' does not exist (read-only mode refuses creation).")
        ro_uri = db_path.resolve().as_uri() + "?mode=ro"

        def _sqlite_ro_creator() -> sqlite3.Connection:
            conn = sqlite3.connect(ro_uri, uri=True, timeout=float(t_sec))
            conn.execute("PRAGMA query_only = ON")
            conn.execute(f"PRAGMA busy_timeout = {t_sec * 1000}")
            return conn

        return create_engine("sqlite://", creator=_sqlite_ro_creator, poolclass=NullPool)

    if d == "MySQL":
        import pymysql

        _ = pymysql
        args: dict = {
            "connect_timeout": t_sec,
            "read_timeout": t_sec,
            "write_timeout": t_sec,
        }
        if p.ssl:
            args["ssl"] = ssl.create_default_context(cafile=p.ssl_ca or None)
        eng = create_engine(
            URL.create(
                "mysql+pymysql",
                username=p.username,
                password=p.password,
                host=p.host,
                port=p.port or 3306,
                database=p.database,
            ),
            connect_args=args,
            poolclass=NullPool,
        )

        @event.listens_for(eng, "connect")
        def _set_mysql_ro(dbapi_conn, _rec):
            # The pinned PyMySQL driver requires TLS before authentication when
            # an explicit context is supplied. Also verify the resulting socket.
            if p.ssl and not isinstance(dbapi_conn._sock, ssl.SSLSocket):
                raise SourceError("MySQL did not establish the required verified TLS connection.")
            cur = dbapi_conn.cursor()
            try:
                cur.execute("SET SESSION TRANSACTION READ ONLY")
            finally:
                cur.close()

        return eng

    if d == "PostgreSQL":
        import psycopg2

        _ = psycopg2
        args = {
            "connect_timeout": t_sec,
            "sslmode": "verify-full" if p.ssl else "prefer",
            "options": f"-c default_transaction_read_only=on -c statement_timeout={t_sec * 1000}",
        }
        if p.ssl_ca:
            args["sslrootcert"] = p.ssl_ca
        return create_engine(
            URL.create(
                "postgresql+psycopg2",
                username=p.username,
                password=p.password,
                host=p.host,
                port=p.port or 5432,
                database=p.database,
            ),
            connect_args=args,
            poolclass=NullPool,
        )

    if d == "SQL Server":
        import pyodbc

        _ = pyodbc
        if p.ssl_ca:
            raise SourceError("SQL Server custom CA files are unsupported. Install the CA in the OS trust store.")
        cs = (
            f"DRIVER={_odbc_value(p.odbc_driver)};SERVER={_odbc_value(f'{p.host},{p.port or 1433}')};"
            f"DATABASE={_odbc_value(p.database)};UID={_odbc_value(p.username)};PWD={_odbc_value(p.password)};"
            f"ApplicationIntent=ReadOnly;Encrypt={'yes' if p.ssl else 'no'};TrustServerCertificate=no;"
        )
        eng = create_engine(
            "mssql+pyodbc:///?odbc_connect=" + quote_plus(cs),
            connect_args={"timeout": t_sec},
            poolclass=NullPool,
        )

        @event.listens_for(eng, "connect")
        def _set_mssql_timeout(dbapi_conn, _rec):
            dbapi_conn.timeout = t_sec
            _enforce_odbc_read_only(dbapi_conn)

        return eng

    raise SourceError(f"Driver '{d}' is not handled by SQLAlchemy.")


def _enforce_odbc_read_only(conn) -> str:
    """Require a backend-enforced boundary, not advisory ODBC access intent.

    SQL_ATTR_ACCESS_MODE/ApplicationIntent may be ignored by a real driver.
    Unknown backends fail closed. SQL Server requires a read-only database or
    replica: routing intent alone cannot prohibit writes to a writable primary.
    """
    import pyodbc

    dbms = str(conn.getinfo(pyodbc.SQL_DBMS_NAME)).strip().lower()
    cur = conn.cursor()
    try:
        if dbms in ("sqlite", "sqlite3"):
            cur.execute("PRAGMA query_only = ON")
            row = cur.execute("PRAGMA query_only").fetchone()
            verified = row is not None and int(row[0]) == 1
        elif dbms in ("postgresql", "postgres"):
            cur.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            cur.execute("SET TRANSACTION READ ONLY")
            row = cur.execute("SHOW transaction_read_only").fetchone()
            verified = row is not None and str(row[0]).lower() == "on"
        elif dbms in ("mysql", "mariadb"):
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            cur.execute("START TRANSACTION READ ONLY")
            # The server must accept both enforcement commands; no silently
            # ignored ODBC attribute or version-dependent system variable.
            verified = True
        elif dbms in ("microsoft sql server", "sql server"):
            row = cur.execute("SELECT DATABASEPROPERTYEX(DB_NAME(), 'Updateability')").fetchone()
            verified = row is not None and str(row[0]).upper() == "READ_ONLY"
        else:
            raise SourceError("The ODBC backend has no verified read-only policy. Use a supported native adapter.")
        if not verified:
            raise SourceError("The database did not confirm enforced read-only access. No user query was executed.")
        return dbms
    finally:
        cur.close()


def _odbc_df(p: ConnectionProfile, sql: str, *, max_rows: int, timeout_seconds: int, params: Mapping | None = None) -> pd.DataFrame:
    import pyodbc

    if p.ssl or p.ssl_ca:
        raise SourceError(
            "Generic ODBC cannot verify the profile TLS policy. Use a native adapter or configure TLS in the driver DSN with profile TLS fields unset."
        )
    cs = p.odbc_string or (
        f"DRIVER={_odbc_value(p.odbc_driver)};DATABASE={_odbc_value(p.database, brace=False)};"
        f"UID={_odbc_value(p.username)};PWD={_odbc_value(p.password)};ApplicationIntent=ReadOnly"
    )
    t_sec = _positive_integer(timeout_seconds, "timeout_seconds")
    conn = pyodbc.connect(cs, timeout=t_sec, readonly=True)
    try:
        conn.timeout = t_sec
        _enforce_odbc_read_only(conn)
        cur = conn.cursor()
        try:
            from sqlalchemy.engine.default import DefaultDialect

            compiled = text(sql).compile(dialect=DefaultDialect(paramstyle="qmark"))
            values = [dict(params or {})[key] for key in compiled.positiontup or ()]
            cur.execute(str(compiled), *values)
            rows = cur.fetchmany(max(1, int(max_rows)))
            cols = [c[0] for c in cur.description] if cur.description else []
            return pd.DataFrame([tuple(r) for r in rows], columns=cols)
        finally:
            cur.close()
    finally:
        conn.close()


def _wrap(exc: Exception, p: ConnectionProfile) -> SourceError:
    if isinstance(exc, ImportError):
        return SourceError(
            f"Driver package for '{p.driver}' is not installed ({exc.name}). Install it or use another source."
        )
    return SourceError(
        f"OFFLINE / UNREACHABLE: could not use the {p.driver} database '{p.name}'. "
        f"The application keeps working offline - check network access and credentials. Details: {safe_database_error(exc)}"
    )


def test_connection(p: ConnectionProfile, *, timeout_seconds: int = DEFAULT_QUERY_TIMEOUT_SECONDS) -> tuple[bool, str]:
    try:
        if p.driver == "ODBC":
            _odbc_df(p, "SELECT 1", max_rows=1, timeout_seconds=timeout_seconds)
        else:
            eng = _engine(p, timeout_seconds=timeout_seconds)
            try:
                with eng.connect() as c:
                    c.execute(text("SELECT 1"))
            finally:
                eng.dispose()
        return True, f"Connection to {p.driver} succeeded (read-only)."
    except SourceError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, str(_wrap(exc, p))


def run_query(
    p: ConnectionProfile,
    sql: str | QuerySpec,
    max_rows: int = DEFAULT_MAX_ROWS,
    *,
    timeout_seconds: int = DEFAULT_QUERY_TIMEOUT_SECONDS,
    params: Mapping | None = None,
) -> pd.DataFrame:
    """Validate ``sql`` via ``sqlglot`` AST and execute in read-only mode bounded by ``max_rows`` and ``timeout_seconds``."""
    _positive_integer(max_rows, "max_rows")
    _positive_integer(timeout_seconds, "timeout_seconds")
    dialect = _DRIVER_TO_DIALECT.get(p.driver)
    if isinstance(sql, QuerySpec):
        if params is not None:
            raise SourceError("Provide bound values through QuerySpec or params, not both.")
        params, sql = sql.params, sql.sql
    s = validate_select(sql, dialect=dialect)
    statement = text(s)
    bound = dict(params or {})
    if set(statement.compile().params) != set(bound):
        raise SourceError("Query parameters are missing or unexpected. Rebuild the query after editing it.")
    try:
        if p.driver == "ODBC":
            return _odbc_df(p, s, max_rows=max_rows, timeout_seconds=timeout_seconds, params=bound)

        eng = _engine(p, timeout_seconds=timeout_seconds)
        try:
            with eng.connect() as c:
                result = c.execution_options(stream_results=True).execute(statement, bound)
                cols = list(result.keys())
                rows = result.fetchmany(int(max_rows))
                return pd.DataFrame([tuple(r) for r in rows], columns=cols)
        finally:
            eng.dispose()
    except SourceError:
        raise
    except Exception as exc:
        raise _wrap(exc, p) from None


# Session credentials are kept in memory when the user does not opt into
# remembering them. Saving metadata must not make the live connection unusable.
_session_credentials: dict[str, dict] = {}


# ------------------------------------------------------------------ encrypted persistence
def save_profile(p: ConnectionProfile, *, remember_credentials: bool = True) -> None:
    blob = encrypt_json({"password": p.password if remember_credentials else "", "queries": p.queries if remember_credentials else {},
                         "odbc_string": p.odbc_string if remember_credentials else ""})
    protection = backend_status()
    opts = json.dumps({"ssl": p.ssl, "ssl_ca": p.ssl_ca, "odbc_driver": p.odbc_driver, "credential_protection": protection})
    with session_scope() as s:
        row = s.query(M.Connection).filter_by(name=p.name).one_or_none()
        if row is None:
            row = M.Connection(name=p.name, driver=p.driver)
            s.add(row)
        (
            row.driver,
            row.host,
            row.port,
            row.database,
            row.username,
            row.options_json,
            row.protected_payload,
        ) = (p.driver, p.host, p.port, p.database, p.username, opts, blob)

    # SQL text may itself contain sensitive literals. Opting out of persistence
    # covers it as well as password/DSN fields, while preserving session use.
    _session_credentials[p.name] = {"password": p.password, "odbc_string": p.odbc_string, "queries": dict(p.queries)}
    if not protection["secure_store"]:
        from .store import audit
        audit("SQL profile uses limited credential protection; review the active backend before relying on persistence.",
              "security", level="WARNING", detail={"backend": protection["name"], "persistent": protection["persistent"]})


def load_profiles() -> list[ConnectionProfile]:
    out = []
    with session_scope() as s:
        for r in s.query(M.Connection).order_by(M.Connection.name).all():
            try:
                sec = decrypt_json(r.protected_payload)
            except Exception as exc:
                log.warning("credentials.profile_unavailable profile=%s error=%s", r.name, type(exc).__name__)
                sec = {}
            sec.update(_session_credentials.get(r.name, {}))
            o = json.loads(r.options_json or "{}")
            out.append(
                ConnectionProfile(
                    r.name,
                    r.driver,
                    r.host,
                    r.port,
                    r.database,
                    r.username,
                    sec.get("password", ""),
                    o.get("ssl", False),
                    o.get("ssl_ca", ""),
                    o.get("odbc_driver", ""),
                    sec.get("odbc_string", ""),
                    sec.get("queries", {}),
                )
            )
    return out


def delete_profile(name: str) -> None:
    _session_credentials.pop(name, None)
    with session_scope() as s:
        s.query(M.Connection).filter_by(name=name).delete()
