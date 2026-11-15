"""Real PostgreSQL connector proof against an explicitly supplied disposable server.

The server must use ephemeral local test authentication. No credentials or
connection URLs are accepted or emitted by this evidence utility. It creates and
removes one temporary-schema table; never point it at production infrastructure.
"""

from __future__ import annotations

import argparse
import getpass
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    import psycopg2
    from energybaseline.core.sqlsource import ConnectionProfile, QuerySpec, SourceError, build_query, run_query, _engine

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    host, database, user = "127.0.0.1", "postgres", getpass.getuser()
    profile = ConnectionProfile(
        name="disposable-postgres-evidence",
        driver="PostgreSQL",
        host=host,
        port=args.port,
        database=database,
        username=user,
    )
    table = "eb_evidence_" + uuid.uuid4().hex
    injection = "x'; DROP TABLE " + table + "; --"
    checks = {}
    with psycopg2.connect(host=host, port=args.port, dbname=database, user=user) as admin:
        admin.autocommit = True
        with admin.cursor() as cursor:
            cursor.execute("SELECT version()")
            version = cursor.fetchone()[0]
            cursor.execute(f'CREATE TABLE "{table}" ("time" timestamp NOT NULL, "label" text, "value" numeric)')
            try:
                cursor.execute(
                    f'INSERT INTO "{table}" VALUES (%s,%s,%s),(%s,%s,%s)',
                    ("2020-01-01", injection, 11, "2020-02-01", "ordinary", 22),
                )
                admin.commit()
                query = build_query(
                    table, ["time", "label", "value"], "time", "2020-01-15", "2020-03-01", dialect="postgres"
                )
                selected = run_query(profile, query)
                checks["date_values_bound_in_real_driver"] = len(selected) == 1 and selected.value.iloc[0] == 22
                literal = run_query(
                    profile, QuerySpec(f'SELECT "label" FROM "{table}" WHERE "label" = :value', (("value", injection),))
                )
                checks["injection_shaped_value_remains_literal"] = (
                    len(literal) == 1 and literal.label.iloc[0] == injection
                )
                checks["table_survives_bound_value"] = (
                    int(run_query(profile, f'SELECT COUNT(*) AS n FROM "{table}"').n.iloc[0]) == 2
                )
                checks["real_row_fetch_limit"] = len(run_query(profile, f'SELECT * FROM "{table}"', max_rows=1)) == 1
                state = run_query(profile, "SELECT current_setting('transaction_read_only') AS state")
                checks["connector_transaction_is_read_only"] = state.state.iloc[0] == "on"
                try:
                    run_query(profile, f'DELETE FROM "{table}"')
                except SourceError:
                    checks["ast_rejects_write"] = True
                else:
                    checks["ast_rejects_write"] = False
                engine = _engine(profile)
                try:
                    from sqlalchemy import text

                    with engine.connect() as connection:
                        try:
                            connection.execute(
                                text(f'INSERT INTO "{table}" VALUES (CURRENT_TIMESTAMP, :label, 99)'),
                                {"label": "must-fail"},
                            )
                        except Exception as exc:
                            # Do not persist backend exception text or query material.
                            checks["database_rejects_write_even_without_ast"] = (
                                getattr(getattr(exc, "orig", None), "pgcode", None) == "25006"
                            )
                        else:
                            checks["database_rejects_write_even_without_ast"] = False
                finally:
                    engine.dispose()
            finally:
                cursor.execute(f'DROP TABLE "{table}"')
    payload = {
        "evidence_schema_version": 1,
        "backend": version,
        "adapter": "PostgreSQL/psycopg2/SQLAlchemy",
        "scope": "Actual disposable Linux PostgreSQL server over TCP; not a mock, hosted-service/TLS or ODBC certification.",
        "checks": checks,
        "passed": all(checks.values()),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
