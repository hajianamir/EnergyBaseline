"""Exercise genuine pyodbc/driver-manager/SQLite-driver behavior, never mocks.

Requires a registered SQLite3 ODBC driver. Creates only a disposable synthetic
SQLite database; accepts no passwords, arbitrary DSNs or external database URLs.
This certifies this driver path only, not SQL Server/MySQL or other ODBC drivers.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main() -> int:
    import pyodbc
    from energybaseline.core.sqlsource import (
        ConnectionProfile,
        QuerySpec,
        SourceError,
        _enforce_odbc_read_only,
        run_query,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver", default="SQLite3", help="Registered local SQLite ODBC driver name.")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.driver not in pyodbc.drivers() or any(c in args.driver for c in "{};\r\n"):
        parser.error("Specify a registered local SQLite driver name without connection-string delimiters.")
    checks = {}
    with tempfile.TemporaryDirectory(prefix="eb_odbc_evidence_") as temp:
        path = Path(temp) / "synthetic.sqlite"
        injection = "x'; DROP TABLE measurements; --"
        with sqlite3.connect(path) as admin:
            admin.execute("CREATE TABLE measurements (time TEXT, label TEXT, value INTEGER)")
            admin.executemany(
                "INSERT INTO measurements VALUES (?, ?, ?)",
                [("2020-01-01", injection, 11), ("2020-02-01", "ordinary", 22)],
            )
        cs = f"DRIVER={{{args.driver}}};Database={path};"
        profile = ConnectionProfile("disposable-odbc-evidence", driver="ODBC", odbc_string=cs)
        connection = pyodbc.connect(cs, readonly=True)
        try:
            metadata = {
                "pyodbc": pyodbc.version,
                "dbms": connection.getinfo(pyodbc.SQL_DBMS_NAME),
                "driver_version": connection.getinfo(pyodbc.SQL_DRIVER_VER),
                "driver_manager": connection.getinfo(pyodbc.SQL_DM_VER),
                "driver_reported_read_only_before_enforcement": connection.getinfo(pyodbc.SQL_DATA_SOURCE_READ_ONLY),
            }
            if str(metadata["dbms"]).lower() not in ("sqlite", "sqlite3"):
                raise RuntimeError("This evidence utility is restricted to the local SQLite fixture.")
            # An actual driver may ignore SQL_ATTR_ACCESS_MODE. Record its real
            # behavior without assuming an advisory flag is a security boundary.
            try:
                connection.execute("INSERT INTO measurements VALUES ('2020-03-01', 'probe', 33)")
                metadata["advisory_flag_prevented_write"] = False
                connection.rollback()
            except pyodbc.Error:
                metadata["advisory_flag_prevented_write"] = True
                connection.rollback()
            checks["backend_policy_installed"] = _enforce_odbc_read_only(connection) in ("sqlite", "sqlite3")
            checks["backend_confirms_query_only"] = connection.execute("PRAGMA query_only").fetchone()[0] == 1
            try:
                connection.execute("INSERT INTO measurements VALUES ('2020-03-01', 'must-fail', 33)")
            except pyodbc.Error as exc:
                checks["database_rejects_write_even_without_ast"] = "readonly" in str(exc).lower()
            else:
                checks["database_rejects_write_even_without_ast"] = False
        finally:
            connection.close()
        found = run_query(
            profile, QuerySpec("SELECT label FROM measurements WHERE label=:label", (("label", injection),))
        )
        checks["injection_shaped_bound_value_remains_literal"] = len(found) == 1 and found.label.iloc[0] == injection
        ordered = run_query(
            profile, QuerySpec("SELECT :second AS b, :first AS a, :second AS repeated", (("first", 17), ("second", 29)))
        )
        checks["qmark_repetition_and_order"] = list(ordered.iloc[0]) == [29, 17, 29]
        dated = run_query(
            profile,
            QuerySpec(
                "SELECT value FROM measurements WHERE time >= :start AND time < :end",
                (("start", "2020-01-15"), ("end", "2020-03-01")),
            ),
        )
        checks["bound_date_range"] = len(dated) == 1 and dated.value.iloc[0] == 22
        checks["bounded_driver_fetch"] = len(run_query(profile, "SELECT * FROM measurements", max_rows=1)) == 1
        checks["table_survives_bound_value"] = (
            int(run_query(profile, "SELECT COUNT(*) AS n FROM measurements").n.iloc[0]) == 2
        )
        try:
            run_query(profile, "DELETE FROM measurements")
        except SourceError:
            checks["ast_rejects_write"] = True
        else:
            checks["ast_rejects_write"] = False
        try:
            run_query(
                profile, QuerySpec("SELECT missing_function(:value)", (("value", "EVIDENCE_CANARY_NOT_A_SECRET"),))
            )
        except SourceError as exc:
            import traceback

            rendered = "".join(traceback.format_exception(exc))
            checks["driver_failure_redacts_bound_value"] = "EVIDENCE_CANARY_NOT_A_SECRET" not in rendered
        else:
            checks["driver_failure_redacts_bound_value"] = False
    checks = {name: bool(result) for name, result in checks.items()}
    payload = {
        "evidence_schema_version": 1,
        "adapter": "pyodbc/unixODBC/SQLite ODBC",
        "native_runtime": True,
        "mocked": False,
        "metadata": metadata,
        "checks": checks,
        "passed": all(checks.values()),
        "scope": "Local synthetic SQLite ODBC only; not SQL Server/MySQL/TLS certification.",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
