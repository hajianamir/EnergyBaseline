"""Policy unit tests; actual driver evidence is remediation_odbc.py, not these fakes."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from energybaseline.core.sqlsource import SourceError, _enforce_odbc_read_only, _odbc_df, ConnectionProfile


class Cursor:
    def __init__(self, value):
        self.value = value
        self.commands = []
        self.closed = False

    def execute(self, statement):
        self.commands.append(statement)
        return self

    def fetchone(self):
        return None if self.value is None else (self.value,)

    def close(self):
        self.closed = True


class Connection:
    def __init__(self, dbms, value):
        self.dbms = dbms
        self.cur = Cursor(value)
        self.closed = False

    def getinfo(self, _code):
        return self.dbms

    def cursor(self):
        return self.cur

    def close(self):
        self.closed = True


@pytest.mark.parametrize(
    "dbms,value,commands",
    [
        ("SQLite", 1, ["PRAGMA query_only = ON", "PRAGMA query_only"]),
        (
            "PostgreSQL",
            "on",
            [
                "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY",
                "SET TRANSACTION READ ONLY",
                "SHOW transaction_read_only",
            ],
        ),
        ("MySQL", None, ["SET SESSION TRANSACTION READ ONLY", "START TRANSACTION READ ONLY"]),
        ("Microsoft SQL Server", "READ_ONLY", ["SELECT DATABASEPROPERTYEX(DB_NAME(), 'Updateability')"]),
    ],
)
def test_explicit_backend_policy_is_required_and_cursor_is_closed(monkeypatch, dbms, value, commands):
    monkeypatch.setitem(sys.modules, "pyodbc", SimpleNamespace(SQL_DBMS_NAME=17))
    conn = Connection(dbms, value)
    assert _enforce_odbc_read_only(conn) == dbms.lower()
    assert conn.cur.commands == commands
    assert conn.cur.closed


@pytest.mark.parametrize(
    "dbms,value",
    [("SQLite", 0), ("PostgreSQL", "off"), ("Microsoft SQL Server", "READ_WRITE"), ("Unknown vendor", "Y")],
)
def test_unconfirmed_enforcement_closes_connection_before_user_sql(monkeypatch, dbms, value):
    conn = Connection(dbms, value)
    monkeypatch.setitem(sys.modules, "pyodbc", SimpleNamespace(SQL_DBMS_NAME=17, connect=lambda *a, **kw: conn))
    with pytest.raises(SourceError):
        _odbc_df(
            ConnectionProfile("unit-only", driver="ODBC"), "SELECT 'must-not-execute'", max_rows=1, timeout_seconds=1
        )
    assert conn.closed and conn.cur.closed
    assert all("must-not-execute" not in statement for statement in conn.cur.commands)


def test_timeout_initialization_failure_still_closes_native_connection(monkeypatch):
    class BrokenTimeout(Connection):
        @property
        def timeout(self):
            return 0

        @timeout.setter
        def timeout(self, value):
            raise RuntimeError("timeout setup failed")

    conn = BrokenTimeout("SQLite", 1)
    monkeypatch.setitem(sys.modules, "pyodbc", SimpleNamespace(connect=lambda *a, **kw: conn))
    with pytest.raises(RuntimeError, match="timeout setup"):
        _odbc_df(ConnectionProfile("unit-only", driver="ODBC"), "SELECT 1", max_rows=1, timeout_seconds=1)
    assert conn.closed
