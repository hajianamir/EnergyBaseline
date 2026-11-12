"""Optional external SQL sources for the Prediction module (the ONLY feature that may need a network).

Drivers: MySQL (pymysql), Microsoft SQL Server (pyodbc), PostgreSQL (psycopg2), SQLite (built-in), generic ODBC (pyodbc).
Passwords and query templates are stored encrypted (Windows DPAPI / keyring+Fernet) - never in plaintext.
Failures raise ``SourceError`` with an offline notice; they never crash the app or block start-up.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import quote_plus

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from ..db import models as M
from ..db.session import session_scope
from .secrets import decrypt_json, encrypt_json

log = logging.getLogger("energybaseline.sql")
DRIVERS = ["SQLite", "MySQL", "SQL Server", "PostgreSQL", "ODBC"]
DEFAULT_PORT = {"MySQL": 3306, "SQL Server": 1433, "PostgreSQL": 5432, "SQLite": None, "ODBC": None}


class SourceError(RuntimeError):
    pass


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


_FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|truncate|exec|execute|merge|grant|revoke|attach|pragma|into)\b", re.I)


def validate_select(sql: str) -> str:
    s = re.sub(r"--.*?$|/\*.*?\*/", " ", sql, flags=re.S | re.M).strip().rstrip(";").strip()
    if not s:
        raise SourceError("The query is empty.")
    if ";" in s:
        raise SourceError("Only a single statement is allowed.")
    if not re.match(r"^(select|with)\b", s, re.I):
        raise SourceError("Only SELECT queries are allowed.")
    bad = _FORBIDDEN.search(s)
    if bad:
        raise SourceError(f"Keyword '{bad.group(0)}' is not allowed (read-only access).")
    return s


_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\[\] ]*$")


def build_query(table: str, columns: list[str], time_col: str | None = None, start: str | None = None, end: str | None = None, limit: int | None = 100000) -> str:
    for ident in [table, *columns, *( [time_col] if time_col else [])]:
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
    return q


def _engine(p: ConnectionProfile):
    d = p.driver
    if d == "SQLite":
        if not p.database:
            raise SourceError("SQLite: please provide the database file path.")
        return create_engine(URL.create("sqlite", database=p.database), connect_args={"timeout": 10})
    if d == "MySQL":
        import pymysql  # noqa: F401
        args = {"connect_timeout": 10}
        if p.ssl:
            args["ssl"] = {"ca": p.ssl_ca} if p.ssl_ca else {"check_hostname": False}
        return create_engine(URL.create("mysql+pymysql", username=p.username, password=p.password, host=p.host, port=p.port or 3306, database=p.database), connect_args=args)
    if d == "PostgreSQL":
        import psycopg2  # noqa: F401
        args = {"connect_timeout": 10, "sslmode": "require" if p.ssl else "prefer"}
        if p.ssl_ca:
            args["sslrootcert"] = p.ssl_ca
        return create_engine(URL.create("postgresql+psycopg2", username=p.username, password=p.password, host=p.host, port=p.port or 5432, database=p.database), connect_args=args)
    if d == "SQL Server":
        import pyodbc  # noqa: F401
        cs = (f"DRIVER={{{p.odbc_driver}}};SERVER={p.host},{p.port or 1433};DATABASE={p.database};UID={p.username};PWD={p.password};"
              f"Encrypt={'yes' if p.ssl else 'no'};TrustServerCertificate={'no' if p.ssl_ca else 'yes'};")
        return create_engine("mssql+pyodbc:///?odbc_connect=" + quote_plus(cs), connect_args={"timeout": 10})
    raise SourceError(f"Driver '{d}' is not handled by SQLAlchemy.")


def _odbc_df(p: ConnectionProfile, sql: str) -> pd.DataFrame:
    import pyodbc
    cs = p.odbc_string or f"DRIVER={{{p.odbc_driver}}};DATABASE={p.database};UID={p.username};PWD={p.password}"
    conn = pyodbc.connect(cs, timeout=10)
    try:
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        return pd.DataFrame([tuple(r) for r in rows], columns=[c[0] for c in cur.description])
    finally:
        conn.close()


def _wrap(exc: Exception, p: ConnectionProfile) -> SourceError:
    if isinstance(exc, ImportError):
        return SourceError(f"Driver package for '{p.driver}' is not installed ({exc.name}). Install it or use another source.")
    return SourceError(f"OFFLINE / UNREACHABLE: could not use the {p.driver} database '{p.name}'. "
                       f"The application keeps working offline - check network access and credentials. Details: {str(exc)[:300]}")


def test_connection(p: ConnectionProfile) -> tuple[bool, str]:
    try:
        if p.driver == "ODBC":
            _odbc_df(p, "SELECT 1")
        else:
            eng = _engine(p)
            with eng.connect() as c:
                c.execute(text("SELECT 1"))
            eng.dispose()
        return True, f"Connection to {p.driver} succeeded."
    except SourceError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, str(_wrap(exc, p))


def run_query(p: ConnectionProfile, sql: str, max_rows: int = 500000) -> pd.DataFrame:
    s = validate_select(sql)
    try:
        if p.driver == "ODBC":
            df = _odbc_df(p, s)
        else:
            eng = _engine(p)
            try:
                with eng.connect() as c:
                    df = pd.read_sql(text(s), c)
            finally:
                eng.dispose()
    except SourceError:
        raise
    except Exception as exc:
        raise _wrap(exc, p) from exc
    if len(df) > max_rows:
        df = df.head(max_rows)
    return df


# ------------------------------------------------------------------ encrypted persistence
def save_profile(p: ConnectionProfile) -> None:
    blob = encrypt_json({"password": p.password, "queries": p.queries, "odbc_string": p.odbc_string})
    opts = json.dumps({"ssl": p.ssl, "ssl_ca": p.ssl_ca, "odbc_driver": p.odbc_driver})
    with session_scope() as s:
        row = s.query(M.Connection).filter_by(name=p.name).one_or_none()
        if row is None:
            row = M.Connection(name=p.name, driver=p.driver)
            s.add(row)
        row.driver, row.host, row.port, row.database, row.username, row.options_json, row.secret_blob = p.driver, p.host, p.port, p.database, p.username, opts, blob


def load_profiles() -> list[ConnectionProfile]:
    out = []
    with session_scope() as s:
        for r in s.query(M.Connection).order_by(M.Connection.name).all():
            try:
                sec = decrypt_json(r.secret_blob)
            except Exception as exc:
                log.warning("cannot decrypt credentials of '%s': %s", r.name, exc)
                sec = {}
            o = json.loads(r.options_json or "{}")
            out.append(ConnectionProfile(r.name, r.driver, r.host, r.port, r.database, r.username, sec.get("password", ""), o.get("ssl", False), o.get("ssl_ca", ""),
                                         o.get("odbc_driver", ""), sec.get("odbc_string", ""), sec.get("queries", {})))
    return out


def delete_profile(name: str) -> None:
    with session_scope() as s:
        s.query(M.Connection).filter_by(name=name).delete()
