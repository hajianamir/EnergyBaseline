"""Probe generated ODBC profiles against a genuine registered SQLite3 driver.

Run alongside remediation_odbc.py: this adds generated-string compatibility
coverage without representing SQLite behavior as MySQL/SQL Server/TLS evidence.
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


def main():
    import pyodbc
    from energybaseline.core.sqlsource import ConnectionProfile, QuerySpec, run_query

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if "SQLite3" not in pyodbc.drivers():
        parser.error("A genuine registered SQLite3 ODBC driver is required")
    with tempfile.TemporaryDirectory(prefix="eb_generated_odbc_") as directory:
        path = Path(directory) / "synthetic.sqlite"
        canary = "value'; DROP TABLE readings; --"
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE readings (value INTEGER, label TEXT)")
            connection.executemany("INSERT INTO readings VALUES (?, ?)", [(17, canary), (29, "ordinary")])
        profile = ConnectionProfile("generated-native-probe", driver="ODBC", odbc_driver="SQLite3", database=str(path))
        rows = run_query(profile, "SELECT value FROM readings ORDER BY value")
        bounded = run_query(profile, "SELECT value FROM readings ORDER BY value", max_rows=1)
        literal = run_query(profile, QuerySpec("SELECT value FROM readings WHERE label=:label", (("label", canary),)))
        checks = {
            "generated_profile_connects": list(rows.value) == [17, 29],
            "bounded_fetch": list(bounded.value) == [17],
            "bound_value_is_literal": list(literal.value) == [17],
            "source_table_preserved": int(run_query(profile, "SELECT COUNT(*) AS n FROM readings").n.iloc[0]) == 2,
        }
    result = {
        "native_runtime": True,
        "mocked": False,
        "adapter": "pyodbc/unixODBC/SQLite3",
        "pyodbc_version": pyodbc.version,
        "checks": checks,
        "passed": all(checks.values()),
        "scope": "Generated local SQLite ODBC profiles only; not remote-backend or TLS certification",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
