# Fresh stepwise code review

## Status

This pass starts from `7963431` on `arena/5b0e8eef-energybaseline`. It is separate from the previous remediation closure. The final full local regression and fresh Windows/Linux CI are **pending**; previous CI is not evidence for these changed product bytes.

## Review sequence and scope

1. Inventory the Python package, run lint/format/undefined-name/dependency checks, and inspect public SDK contracts.
2. Inspect ingestion, external SQL construction, TLS configuration, encrypted credentials, executable-artifact approval, paths and archive boundaries.
3. Trace training-only fold preparation, temporal history, purge/nested evaluation and inference authorization.
4. Inspect UQ, causal and monitoring gateways and read-only baseline behavior.
5. Inspect the immutable report contract, chart palette handling, HTML/CSV/Excel/PDF projections and archive agreement.
6. Add regression tests for reproducible defects, then run the unchanged existing suite plus the new tests, architecture guards and native adapter probes.

Static tooling covers the package. Manual inspection is risk-focused; neither it nor a green suite proves the absence of every possible defect in every input, optional backend or deployment environment.

## Confirmed defects and corrections

| Area | Reproduced defect | Correction |
| --- | --- | --- |
| MySQL TLS | Profile TLS could disable hostname checking | Use a default-verifying `SSLContext`; check certificate and hostname. Verify the pinned driver's pre-authentication refusal when the server does not advertise TLS, and verify the established socket as defense in depth. |
| PostgreSQL TLS | `require` encrypted without hostname verification | Use `verify-full` for explicitly requested TLS; retain optional root-CA support. |
| SQL Server TLS | TLS without a custom CA selected `TrustServerCertificate=yes`; the CA path itself was not actually consumed | Always reject untrusted certificates. Reject unsupported per-profile CA files explicitly; install trusted CAs in the OS trust store. |
| Generated ODBC strings | Delimiters in host/database/user/password could add attributes | Brace attributes, escape closing braces and reject control characters. Permit only safe delimiter-free unpadded Database paths without braces for legacy SQLite ODBC compatibility. Caller-provided full ODBC strings remain explicit driver configuration, not generated values. |
| Generic ODBC TLS | Profile TLS fields were silently ignored | Reject these fields rather than imply verification; use a native adapter or explicitly configure transport policy in the driver DSN. |
| Query bounds | Booleans/floats/nonfinite values could pass numeric comparisons or fail with incidental errors | Require positive integer row/time bounds before connecting. |
| SDK | Falsey malformed configurations were replaced with an empty mapping | Default only `None`; preserve validation errors for falsey non-mappings. |
| Request dictionaries | Missing required fields raised constructor `TypeError` | Raise an explicit contract `ValueError`. |
| CSV detection | Long single-line CSV content was probed as a filename and could raise `OSError` | Treat failed implicit path probes as content; explicit `Path` inputs retain file semantics. |
| Inference gateways | UQ/drift/causal/optional-causal/F1–F10 checked acceptance but not an unchanged preregistration lock | Share the lock-and-digest check; bind F1–F10 calibration inputs to their VSG as well. No implicit locking is introduced in these gateways. |
| Uncertainty | NaN/infinite standard uncertainties could produce invalid bands | Require finite, nonnegative measurement/static-factor uncertainty inputs. |
| Charts | A valid single-color approved palette caused indexed color lookup failures | Cycle the accepted nonempty palette for renderer color slots. |
| Excel | A Contents title could be a formula; apostrophes broke internal links | Write all appended strings as literals and escape sheet-title apostrophes in links. |
| Excel fidelity | Length was checked after openpyxl silently truncated text | Check original row values before creating cells; fail explicitly and retain the JSON companion as the full-fidelity path. |
| HTML agreement | The reader assumed every metric artifact ID started with `metric.` | Read all IDs allowed by ReportModel, only from the authoritative metric table; reject duplicate metric IDs. |

## Regression evidence

Evidence directory: `artifacts/remediation/stepwise-review/`.

- `reproduced-before.txt`: **40 failed, 2 passed** before the initial fixes. Parameterized failures are not 40 independent root causes.
- `additional-before.txt`: **2 failed** reproducing the oversized-Excel and generic-HTML-ID defects before those fixes.
- `focused.txt` / `focused.xml`: **54 passed**, including additional transport-policy and pinned-driver defenses.
- `static.txt` / `static.json`: **10 checks passed** (pip check, Ruff lint/format, Pyflakes, compileall, four architecture/integrity guards and diff whitespace validation).
- `native-generated-odbc.json`: **4 actual checks passed**, generated SQLite3 ODBC profile, bound values and row bounds; no mocks.
- `native-odbc.json`: **10 actual checks passed**, including database-enforced read-only behavior; no mocks.
- `full-interim-stopped.txt`: superseded incomplete run, stopped to add transport/compatibility defenses; **not acceptance evidence**.
- `full.txt` / `full.xml`: final full run in progress.

No pre-existing test assertions were changed, removed, skipped or weakened in this pass. LICENSE and `artifacts/review_baseline/` remain unchanged.

## Compatibility and remaining deployment evidence

- Previously accepted but unlocked panels must now be explicitly preregistered before these inferential gateways run. A changed locked specification must be amended/revalidated, not silently accepted.
- Explicit TLS can now reject self-signed/untrusted certificates or hostname mismatches that were previously accepted. This is intentional, not an invitation to disable verification.
- SQL Server custom CA files are not silently supported. Generic ODBC TLS remains driver/DSN-specific and is not certified by a checkbox.
- Driver-configuration tests use captured SQLAlchemy options and controlled failure boundaries. Actual ODBC probes exercise a genuine local SQLite driver. **Neither is real MySQL/SQL Server or remote TLS-handshake certification.**
- The previous closure's physical reference-machine, OS reboot/locked-desktop and production signing limitations remain; they are not covered by this pass.
- No PR, main-branch modification or merge is part of this work.
