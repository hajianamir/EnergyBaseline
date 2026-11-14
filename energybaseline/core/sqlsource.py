"""Read-only external SQL sources validated via sqlglot AST and driver read-only mode (AC-06, DAT-7)."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import re
import sqlite3
from urllib.parse import quote_plus

import pandas as pd
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool
import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from ..db import models as M
from ..db.session import session_scope
from .secrets import decrypt_json, encrypt_json

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
    password: str = ""
    ssl: bool = False
    ssl_ca: str = ""
    odbc_driver: str = "ODBC Driver 18 for SQL Server"
    odbc_string: str = ""
    queries: dict = field(default_factory=dict)  # template name -> SQL


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
    except SqlglotError as exc:
        raise SourceError(f"SQL syntax / read-only AST validation failed: {exc}") from exc

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


_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\[\] ]*$")


def build_query(
    table: str,
    columns: list[str],
    time_col: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int | None = 100_000,
) -> str:
    for ident in [table, *columns, *([time_col] if time_col else [])]:
        if not _IDENT.match(ident):
            raise SourceError(f"Invalid identifier '{ident}'.")
    q = f"SELECT {', '.join(columns) if columns else '*'} FROM {table}"
    cond = []
    if time_col and start:
        cond.append(f"{time_col} >= '{start.replace(chr(39), '')}'")
    if time_col and end:
        cond.append(f"{time_col} <= '{end.replace(chr(39), '')}'")
    if cond:
        q += " WHERE " + " AND ".join(cond)
    if time_col:
        q += f" ORDER BY {time_col}"
    if limit is not None and int(limit) > 0:
        q += f" LIMIT {int(limit)}"
    return validate_select(q)


def _engine(p: ConnectionProfile, *, timeout_seconds: int = DEFAULT_QUERY_TIMEOUT_SECONDS):
    """Create a read-only database engine with explicit connection/statement timeouts (AC-06, DAT-7)."""
    d = p.driver
    t_sec = max(1, int(timeout_seconds))
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
            args["ssl"] = {"ca": p.ssl_ca} if p.ssl_ca else {"check_hostname": False}
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
            "sslmode": "require" if p.ssl else "prefer",
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
        cs = (
            f"DRIVER={{{p.odbc_driver}}};SERVER={p.host},{p.port or 1433};DATABASE={p.database};"
            f"UID={p.username};PWD={p.password};ApplicationIntent=ReadOnly;"
            f"Encrypt={'yes' if p.ssl else 'no'};TrustServerCertificate={'no' if p.ssl_ca else 'yes'};"
        )
        eng = create_engine(
            "mssql+pyodbc:///?odbc_connect=" + quote_plus(cs),
            connect_args={"timeout": t_sec},
            poolclass=NullPool,
        )

        @event.listens_for(eng, "connect")
        def _set_mssql_timeout(dbapi_conn, _rec):
            dbapi_conn.timeout = t_sec

        return eng

    raise SourceError(f"Driver '{d}' is not handled by SQLAlchemy.")


def _odbc_df(p: ConnectionProfile, sql: str, *, max_rows: int, timeout_seconds: int) -> pd.DataFrame:
    import pyodbc

    cs = p.odbc_string or (
        f"DRIVER={{{p.odbc_driver}}};DATABASE={p.database};UID={p.username};PWD={p.password};ApplicationIntent=ReadOnly"
    )
    t_sec = max(1, int(timeout_seconds))
    conn = pyodbc.connect(cs, timeout=t_sec, readonly=True)
    conn.timeout = t_sec
    try:
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchmany(max(1, int(max_rows)))
        cols = [c[0] for c in cur.description] if cur.description else []
        return pd.DataFrame([tuple(r) for r in rows], columns=cols)
    finally:
        conn.close()


def _wrap(exc: Exception, p: ConnectionProfile) -> SourceError:
    if isinstance(exc, ImportError):
        return SourceError(
            f"Driver package for '{p.driver}' is not installed ({exc.name}). Install it or use another source."
        )
    return SourceError(
        f"OFFLINE / UNREACHABLE: could not use the {p.driver} database '{p.name}'. "
        f"The application keeps working offline - check network access and credentials. Details: {str(exc)[:300]}"
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
    sql: str,
    max_rows: int = DEFAULT_MAX_ROWS,
    *,
    timeout_seconds: int = DEFAULT_QUERY_TIMEOUT_SECONDS,
) -> pd.DataFrame:
    """Validate ``sql`` via ``sqlglot`` AST and execute in read-only mode bounded by ``max_rows`` and ``timeout_seconds``."""
    if max_rows <= 0:
        raise SourceError("max_rows must be a positive integer.")
    if timeout_seconds <= 0:
        raise SourceError("timeout_seconds must be a positive integer.")
    dialect = _DRIVER_TO_DIALECT.get(p.driver)
    s = validate_select(sql, dialect=dialect)
    try:
        if p.driver == "ODBC":
            return _odbc_df(p, s, max_rows=max_rows, timeout_seconds=timeout_seconds)

        eng = _engine(p, timeout_seconds=timeout_seconds)
        try:
            with eng.connect() as c:
                result = c.execution_options(stream_results=True).execute(text(s))
                cols = list(result.keys())
                rows = result.fetchmany(int(max_rows))
                return pd.DataFrame([tuple(r) for r in rows], columns=cols)
        finally:
            eng.dispose()
    except SourceError:
        raise
    except Exception as exc:
        raise _wrap(exc, p) from exc


# ------------------------------------------------------------------ encrypted persistence
def save_profile(p: ConnectionProfile) -> None:
    blob = encrypt_json({"password": p.password, "queries": p.queries, "odbc_string": p.odbc_string})
    opts = json.dumps({"ssl": p.ssl, "ssl_ca": p.ssl_ca, "odbc_driver": p.odbc_driver})
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


def load_profiles() -> list[ConnectionProfile]:
    out = []
    with session_scope() as s:
        for r in s.query(M.Connection).order_by(M.Connection.name).all():
            try:
                sec = decrypt_json(r.protected_payload)
            except Exception as exc:
                log.warning("cannot decrypt credentials of '%s': %s", r.name, exc)
                sec = {}
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
    with session_scope() as s:
        s.query(M.Connection).filter_by(name=name).delete()
