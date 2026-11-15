# Additional closure execution: native ODBC and raw product evidence

This is a later checkpoint than `REMEDIATION_NATIVE_EXECUTION.md`. Historical
counts and source manifests do not certify this subsequent tree. The owner
explicitly authorized updating the one outdated GUI contract test and committing
and pushing this current Arena branch for actual CI. No other original test,
LICENSE or `artifacts/review_baseline/` is modified. Release acceptance is still
subject to the actual results below, not workflow preparation alone.

## Actual ODBC execution exposed and fixed a security defect

A genuine pyodbc 5.5.0 -> unixODBC 2.3.1 -> SQLite ODBC 0.99991 -> system SQLite
connection was built and executed locally, without mocks. The driver accepted
`readonly=True` yet reported writable access and accepted a write. Its transaction
was rolled back in the disposable synthetic database. This demonstrates why
ODBC access-mode flags and SQL Server ApplicationIntent are not enforcement.

`energybaseline/core/sqlsource.py` now installs a backend-native policy before
executing any user query:

| Backend detected by ODBC | Required policy |
| --- | --- |
| SQLite | Set `PRAGMA query_only=ON`, then read back `1` |
| PostgreSQL | Set session and current transaction read-only, verify `SHOW transaction_read_only` |
| MySQL/MariaDB | Server must accept session and explicit current read-only transaction commands |
| SQL Server | Database/replica must report `DATABASEPROPERTYEX(..., 'Updateability') = READ_ONLY` |
| Unknown | Fail closed before user SQL |

The SQL Server SQLAlchemy adapter uses the same enforcement; a writable primary
is intentionally refused even with ApplicationIntent=ReadOnly. Read-only-login
permissions alone are not currently a verified substitute for a read-only
database/replica. This is a deliberate compatibility restriction, not evidence
that a real SQL Server instance has been validated here.

Connection and cursor cleanup now covers timeout-initialization and enforcement
failure too. ODBC parameter compilation uses the backend-neutral SQLAlchemy
qmark dialect, rather than implicitly treating every ODBC backend as SQL Server.

`odbc-native.json` records ten actual checks: enforcement installation/readback,
write rejection even without AST validation, injection-shaped literal binding,
repeated parameter order, bound date range, row-fetch bound, surviving table,
AST write rejection and redacted driver failure. No credentials or DSNs are
exported. The fixture creates and removes only its own synthetic database.

## Reproducibility and scope

Use maintained distribution packages for deployed ODBC libraries. A sandbox-only
fallback exists when distribution packages cannot be installed:

```sh
.venv/bin/python scripts/evidence/bootstrap_odbc.py
ODBCSYSINI="$PWD/.cache/odbc-rebuild/config" \
  LD_LIBRARY_PATH="$PWD/.cache/odbc-rebuild/prefix/lib" \
  .venv/bin/python scripts/evidence/remediation_odbc.py \
    --out artifacts/remediation/closure/odbc-native.json
```

The fallback checks archive SHA-256 and embedded Git commit IDs. It rebuilds a
real, old test-only unixODBC manager and SQLite driver, with genuine SQLite
headers and the system runtime library. It does not introduce product runtime
dependencies or certify those old library versions for deployment. All sources,
configuration and binaries stay under ignored `.cache/`; only provenance and
logs are retained. `odbc-bootstrap-verification.txt` records a successful fresh
rebuild and native execution, exit code 0. Mocks in the nine added policy unit
tests are explicitly not counted as native evidence.

## Evidence location and source identity

New evidence is under `artifacts/remediation/closure/`:

- `odbc-native.json` / `.txt`: 10/10 real ODBC checks passed.
- `odbc-runtime-sources.json`, `unixodbc-build.txt`, `sqliteodbc-build.txt` and
  `odbc-bootstrap-verification.txt`: pinned provenance, compilation and an
  independently rebuilt native execution.
- `pytest-sql-boundary.txt`: 91 tests passed in 15.27 seconds, covering the new
  enforcement policy plus original SQL ingestion and security regressions.
- `static-checks.txt`: 11 commands, zero failures, including frozen original
  tests/LICENSE/review baseline integrity.
- `source-manifest.json`: exact source hashes for the current uncommitted tree.
  A product-generated phase manifest's `source_commit` identifies HEAD only;
  the supplemental source manifest identifies actual tested dirty-tree content.
- `phase7-raw.txt` and `phase7/`: actual product evidence for UQ, drift/monitoring,
  causal analysis and the report fuzz corpus. This is computational evidence,
  not a distribution-free guarantee for autocorrelated data or a reference-
  hardware capacity certificate.
- `phase7-raw.txt`: product evidence generator completed with exit code 0.
- `phase8-raw.txt` and `phase8/`: actual gated analysis and native preview,
  PDF/Excel/HTML renderers and audit archive generation completed with exit code
  0. All 20 key metrics agree across PDF, Excel and native preview; Executive PDF
  is 9 pages and byte-deterministic on repeated rendering. Archive verification
  reproduced 20 metrics and 30 tables and verified 8 files. These are real local
  E1/E2 components, not Windows/two-OS E3 or complete release acceptance.
- `source-manifest.json` identifies 210 source/configuration files. No source drift
  was found after the product evidence generators completed.
- Final full-suite outcome is recorded after the remaining command completes.

## Gates that local implementation cannot silently close

1. **Authorized GUI contract correction implemented:** the owner explicitly
   allowed updating `tests/gui/test_phase6_gui_top5_and_lock.py`. It now selects
   the actual surrogate card and compares the displayed fidelity to artifact
   metadata. All register/visibility/SC-7/lock assertions remain; the test also
   rejects an incorrect independently supplied artifact digest before approving
   the correct producer-captured digest and completing the lock. No metric,
   ranking, skip or xfail workaround is used. This corrected test and the added
   native GUI regressions passed: **5 tests in 25.82 seconds**.
2. This machine is Linux, not Windows. The owner explicitly authorized commit
   and push on `arena/5b0e8eef-energybaseline` for CI, without PR, main changes or
   merge. Repository push permission was confirmed. An Actions permissions API
   request returned HTTP 403, while workflow listing succeeded; actual push/
   dispatch and run retrieval must be attempted and their results recorded.
   Older remote runs cannot certify this source. The pre-authorization local
   full run was intentionally stopped before editing the authorized test and is
   retained as `pytest-preapproval-stopped.txt`, not an acceptance run.
3. There is no active native Secret Service/credential-store service here. Session
   fallback evidence is not secure-store durability evidence. Windows DPAPI and
   other OS-backed persistence require execution on those actual OS/services.
4. Real SQLite ODBC evidence narrows the previous adapter gap, but is not evidence
   for SQL Server, MySQL/MariaDB, hosted services, TLS policies or other ODBC
   drivers. Server-native policies outside SQLite currently have unit coverage
   only in this checkpoint.
5. Additional Linux raw logs do not replace the entire original E1/E2/E3 package,
   physical reference-machine gates, release signing or a successful coverage
   gate. Any missing category remains open until its actual evidence exists.
