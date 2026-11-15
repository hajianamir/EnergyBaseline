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
- `pytest-final.txt` and `.xml`: **438 passed in 664.18 seconds**, zero errors,
  failures or skips, on pushed commit `b7e3caa313ac14981eb51d560645c89ce6b77cfb`.
  The authorized GUI test now passes without fabricating fidelity or bypassing
  artifact approval.
- The first real Windows CI run exposed two portability errors in the newly
  added failure-injection unit fixture, not in the native DPAPI probe: mocking
  the public keyring facade did not disable the genuine Windows provider, and
  POSIX permission bits are not Windows ACLs. The fixture now patches the actual
  provider boundary and only applies the POSIX mode assertion on POSIX, without
  skipping either test. All 53 related local tests passed in 4.37 seconds.
- Follow-up pushed commit `e03742be4fa7ad27e155169ad01ce15d2cc91898` changes only
  that added test fixture and CI configuration; **all product Python source bytes
  match the 438-pass local snapshot**. `ci-followup-source-manifest.json` records
  its exact source. `static-authorized-final.txt` records 11 successful checks,
  including all original tests except the one explicitly authorized GUI update.
- CI now publishes native evidence JSON/hash receipts in check notices as well
  as artifacts, because this sandbox cannot download Azure-hosted artifacts.
  Evidence/docs-only pushes do not repeat unchanged scientific tests; product,
  test and workflow changes still trigger CI. No test is skipped or xfailed.
- **Corrected two-OS CI is green:** run `43685905726` completed successfully for
  both `windows-latest` and `ubuntu-latest` on source commit
  `e03742be4fa7ad27e155169ad01ce15d2cc91898`:
  <https://github.com/hajianamir/EnergyBaseline/actions/runs/43685905726>.
  `ci-final.json` records actual job/step outcomes and timings, including the
  complete tests/coverage gate, native GUI gate, renderer/audit gates, Windows
  packaging/runtime evidence, signing smoke, dependency audit and SBOM steps.
- `ci-native/windows-credentials.json`, `linux-credentials.json` and
  `linux-odbc.json` are **actual CI-produced native evidence**, all passed. They
  were retrieved through official check notices, reconstructed byte-for-byte
  (including Windows CRLF) and matched to the SHA-256 computed by the CI runner.
  `ci-native-receipt-provenance.json` and `ci-*-annotations.json` bind those
  bytes to jobs and the tested commit. This is not mocked evidence and not an
  assertion that an inaccessible Azure artifact ZIP was downloaded.
- Whole-job Azure-hosted logs/ZIP downloads remain inaccessible from this
  sandbox. The official raw GitHub job/step metadata and verified native JSON
  receipts are retained; no nonexistent download or physical-machine execution
  is claimed. The original failed Windows run is retained separately and is not
  presented as the successful E3 run.

## Closure decisions and remaining deployment gates

1. **Authorized GUI contract correction implemented:** the owner explicitly
   allowed updating `tests/gui/test_phase6_gui_top5_and_lock.py`. It now selects
   the actual surrogate card and compares the displayed fidelity to artifact
   metadata. All register/visibility/SC-7/lock assertions remain; the test also
   rejects an incorrect independently supplied artifact digest before approving
   the correct producer-captured digest and completing the lock. No metric,
   ranking, skip or xfail workaround is used. This corrected test and the added
   native GUI regressions passed: **5 tests in 25.82 seconds**.
2. **Actual two-OS CI execution completed:** the owner explicitly authorized
   commit and push on `arena/5b0e8eef-energybaseline`, without PR, main changes
   or merge. Push succeeded and triggered the two-OS run documented above. The
   initially denied Actions permissions endpoint did not prevent execution or
   job/check-result retrieval. The pre-authorization local run was intentionally
   stopped before editing the authorized test and remains
   `pytest-preapproval-stopped.txt`, not an acceptance run.
3. The local sandbox has no active native credential-store service; its new
   native probe correctly returns a failed secure-storage gate while confirming
   session expiry and actual disconnected-service fallback. However, authorized
   CI run `43684840347` on source commit
   `b7e3caa313ac14981eb51d560645c89ce6b77cfb` has now actually executed and passed
   both the Windows DPAPI fresh-process/tamper probe and Linux Secret Service
   fresh-process/real-bus-outage probe. Linux's native SQLite ODBC step also
   passed. The corrected run subsequently passed both complete jobs, and all
   three native JSON receipts were retrieved and SHA-256 verified as documented
   above. These probes cover application-process restart, not OS reboot or
   physical desktop checks.
4. Real SQLite ODBC evidence narrows the previous adapter gap, but is not evidence
   for SQL Server, MySQL/MariaDB, hosted services, TLS policies or other ODBC
   drivers. Server-native policies outside SQLite currently have unit coverage
   only in this checkpoint.
5. Both CI coverage gates passed; no unobserved numeric coverage percentage is
   invented. The successful CI/native proofs do not certify physical reference-
   machine performance, OS reboot recovery, production Authenticode signing or
   every deployment adapter/TLS policy. Those distinct release/deployment claims
   remain open until their own actual evidence exists.
