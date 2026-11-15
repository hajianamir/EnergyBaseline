# Python 3.12 migration evidence index

Main report: `docs/PYTHON312_MIGRATION.md`.

**Final acceptance:** CI run **43704827147** succeeded on both Windows (`132783541078`) and Ubuntu 26.04 (`132783541242`), source `56574a4`. This source differs from the clean local 498-pass source only in README documentation. See `summary.json`, `source-identity.json`, `source-binding-ci-final.json` and `ci/receipts-final.json`.

## Accepted local execution

- `pytest-isolation.txt`, `pytest-isolation.xml`: full standard suite, 498 passed, zero failed/skipped, clean process exit 0; 646.40 seconds, source `f0a6981d10cbab5ebd4365cfbe86b5886ea1c4bb`.
- `gui-isolation.txt`, `gui-isolation.xml`: all 17 GUI cases, real worker spawn included, strict native-widget teardown assertions, exit 0.
- `static-final.json`: 11 static/runtime/dependency checks, all exit 0.
- `local-run-status.json`: distinguishes clean acceptance from earlier failed/interrupted runs.
- `source-binding-final.json`: SHA-256 binding for the locally tested tracked source. Later README-only CI retry does not change any executable/test/dependency/workflow file.

## Failed or diagnostic runs — not acceptance

- `pytest.txt`/XML and `coverage.xml`: 498 passing test assertions, 85.66% core line coverage, but process exit 139 during Qt teardown. Not a clean run.
- `pytest-fixed.txt`/XML: 498 passing assertions on the first production ownership fix, but exit 139 persisted. Not a clean run.
- `pytest-clean-exit.txt`: interrupted investigation on superseded source, not acceptance.
- `qt-isolation.txt`: ad-hoc diagnostic wrapper lacked a multiprocessing `__main__` guard; its worker timeout is not standard pytest/CI evidence.
- `failed-ci/`: official GitHub annotations, retaining the initial Linux exit 139 and later Windows 5.297-second startup timing failure against its unchanged 5.0-second test limit.

## Native and report evidence

- `native/`: actual local pyodbc/unixODBC/SQLite3 execution, generated-profile 4/4 and read-only/security 10/10 checks. Not remote MySQL/SQL Server/TLS certification.
- `corrected-phase7/`: fresh product APIs, 11 verified hashes, all UQ crosschecks within tolerance.
- `corrected-phase8/`: fresh HTML/PDF/Excel/Qt report agreement, 20 artifact-bound metrics, 9-page deterministic Executive PDF, reproducible 8-file audit archive, 12 manifest hashes. Source `6511457` has identical product/dependency/workflow files to the final test-isolation source; only test-harness/documentation changes follow.
- `phase7/`, `phase8/`: preceding d848936 product evidence, retained with its original source provenance.
- `ci/`: official GitHub run/job/annotation responses and runner-SHA-verified native evidence receipts. Receipt JSON is reconstructed from official annotations, not claimed as an Azure artifact ZIP download. The linked hosted jobs use the shared Python 3.12 runtime preflight.

## Environment/reproduction limits

- `runtime-build.sh`, `runtime-build.txt`: local CPython 3.12.16 source-build procedure/log, using upstream source tags and disposable native development libraries.
- `environment.json`, `install.txt`, `resolver.txt`: installed environment and Python-3.12 universal hashed dependency resolution. Fifteen existing CUDA native-library distributions with exactly matching lock versions were reused by local symlink to fit the disk limit. Hosted CI installs the lock normally; the local environment is not described as a clean-room install.
- Production Authenticode signing, physical-machine performance, desktop reboot/lock-state persistence and real remote-database TLS remain outside this migration's certification scope. A passing retry does not erase the retained Windows startup timing sensitivity.
