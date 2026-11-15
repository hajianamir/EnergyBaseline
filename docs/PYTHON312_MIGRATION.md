# Python 3.12 migration and main integration

## Final result — migration and native validation complete

All implementation work remains on `arena/5b0e8eef-energybaseline`. Remote main `fed56d58d18c20e0a689b5f510ed2e7c357034d9` was integrated locally by merge commit `d7fc8f9`; no main push, new PR, or PR merge/close operation was performed.

| Validation | Final evidence |
|---|---|
| Complete local Python 3.12.16 suite | **498 passed, 0 failed, 0 skipped, process exit 0**, 646.40 seconds; source `f0a6981` |
| Final real Windows CI | **SUCCESS**, job `132783541078` |
| Final real Ubuntu 26.04 CI | **SUCCESS**, job `132783541242` |
| Shared final CI run | [43704827147](https://github.com/hajianamir/EnergyBaseline/actions/runs/43704827147), tested source `56574a4970490735053a563c01d8616fc4f28ebf` |
| Source identity | Local full-suite source and final CI source differ only in root README documentation; all executable, dependency, workflow and test files are identical |
| Static/runtime/dependency checks | **11 passed**; actual interpreter and `pip check` verified |
| Preserved gates | Full-suite core/selected-module coverage gates and GUI coverage gate passed on both hosted OSes; no test or coverage threshold was weakened |
| Native receipts | Three actual hosted ODBC/SecretService/Windows-DPAPI receipts, byte-exact runner SHA-256 verified from official check annotations |
| Source binding | **294 files**, **0 drift**; eight CRLF working-tree scripts also verified against Git's normalized blob identity |
| Fresh product reports | Four-renderer agreement on **20 metrics**, **9-page** deterministic Executive PDF, **8-file** reproducible audit archive, **11 + 12** phase manifest hashes verified |

Windows CI also passed executable packaging/runtime evidence, the self-signed Authenticode smoke test, vulnerability audit, SBOM generation and artifact upload. These hosted smoke checks are not production-signing or physical-machine certification.

**Retained findings:** initial local/hosted Linux processes segfaulted after passing assertions because Qt objects survived into interpreter teardown. The final production ownership cleanup and strict GUI test destruction contract now pass with clean exit, including two native Linux runs. A separate Windows initialization timing check once took **5.297 seconds against its unchanged 5.0-second limit**; the identical-implementation retry passed. The failure is retained, and a successful retry does not erase timing sensitivity under hosted load.

The exact/hash dependency manifests contain 164 universal Python 3.12 package pins. Native Qt Essentials/Addons avoid the WebEngine dependency newly introduced by the PySide6 metapackage. The original four integrity-protected tests, LICENSE and `artifacts/review_baseline/` remain unchanged; the two existing runtime contract tests were updated only for the explicitly authorized migration, and new semantic/GUI/teardown assertions strengthen coverage.

Primary machine-readable evidence: `artifacts/remediation/python312/summary.json`, `pytest-isolation.txt`/XML, `static-final.json`, `source-identity.json`, `source-binding-ci-final.json`, `ci/receipts-final.json`, and `artifact-sha256.json`. The historical Python 3.11 review is not used as migration acceptance. The final evidence commit changes only `docs/**` and `artifacts/**`, which the existing workflow intentionally ignores; no validated implementation changes follow the tested source.

**Scope limits:** local matching CUDA native libraries were reused to fit the sandbox disk limit; hosted installs are fresh. Native receipt bytes were reconstructed from official check annotations and verified against runner SHA-256, not downloaded as Azure ZIPs. Production signing, real remote MySQL/SQL Server TLS, reboot/locked-desktop persistence and physical-machine performance remain uncertified.

## Chronological execution record

The entries below retain intermediate pending states and failed attempts for auditability; the final table above is the current acceptance status.

## Authorized scope

Integrate `origin/main` (`fed56d58d18c20e0a689b5f510ed2e7c357034d9`) into `arena/5b0e8eef-energybaseline`, retain the remediation/security contracts, migrate the complete runtime policy to Python 3.12, regenerate dependency manifests, and execute fresh Windows/Linux CI. Do not change main, merge/close PRs, remove tests or weaken coverage.

## Implementation

- `.python-version` declares `3.12`. Both Actions workflows consume that file and resolve the latest published patch available for their runner. A single exact patch is not assumed to exist on both Windows and Ubuntu 26.04.
- `pyproject.toml` requires `>=3.12,<3.13`; Ruff targets `py312`. Installation rejects an existing non-3.12 virtualenv. Both workflows verify the actual interpreter and run `pip check` before tests.
- Direct pins from main were resolved with uv 0.13.8 using `--python-version 3.12 --universal --generate-hashes`. The exact manifest and hashed lock share 164 package pins. `requirements-dev.txt` continues to reference the exact runtime manifest and retain all original gates/tools.
- PySide6 6.12's metapackage newly includes PySide6-WebEngine/Pdf. Native `PySide6-Essentials==6.12.0` and `PySide6-Addons==6.12.0` retain Qt widgets/charts/SVG without introducing a browser dependency. This is asserted in tests and the existing web-free gate remains enforced.
- Keep main's window/icon/theme and statistical catalog improvements. Preserve explicitly selected targets across dataset reloads without enabling energy-name heuristics outside the energy domain. Derive schema target roles through ColumnSpec rather than assigning to the read-only `DatasetSchema.target_columns` property. New GUI regression covers both neutral and energy domains, stale role metadata, collect/reload, and maximizable setup windows.
- Do not adopt main's synthetic daily fallback when no timestamp is selected. It bypassed existing schema/temporal validation and invented granularity. Preserve the established temporal implementation and explicit timestamp requirement, with all existing temporal tests unchanged.
- Existing workflow contract tests now assert the authorized 3.12 policy; a new semantic contract checks shared version declarations, excluded unsupported minors, manifest equality and preflight ordering. No tests were removed or skipped.

## Evidence status

The historical 493-pass Python 3.11 review and CI run 43691420952 are not Python 3.12 evidence.

Initial integration preflight on the existing Python 3.11 environment: 11 runtime/workflow structure tests passed; 15 existing GUI tests passed in 45.23 seconds; two new integration GUI cases passed. These are compatibility preflights, not migration acceptance. Ruff lint/format, pyflakes, compileall, web-free guard, baseline-test integrity and requirements-matrix checks passed.

The sandbox's prebuilt Python 3.12 download is blocked by the outbound release-asset host restriction. A local CPython 3.12.16 source build is being attempted with explicit native development headers/libraries. Native 3.12 execution, full tests and fresh two-OS CI results will be recorded below when available. No completion claim is made from the preflight alone.

## Reproduction

```sh
python -m pip install uv==0.13.8
uv pip compile requirements.in --python-version 3.12 --universal --generate-hashes --output-file /tmp/requirements312.lock
# The committed lock removes resolver annotations, not package pins or hashes.
python -m pip install --require-hashes -r requirements.lock
python -m pip install -r requirements-dev.txt
python -m pip install --no-build-isolation --no-deps -e .
python scripts/verify_runtime.py
python -m pip check
python -m pytest tests/ --cov=energybaseline/core --cov-fail-under=60
```

Linux GUI execution additionally requires the native Qt/ODBC/SecretService packages declared in CI. Native credential and ODBC evidence must come from actual OS services, not mocked substitutes. Packaging/production signing and real remote database TLS retain their existing certification limits.

### Local 3.12 environment established

CPython 3.12.16 was built from the upstream `v3.12.16` source tag in the disposable cache, with OpenSSL 3.0.18 and native zlib/SQLite/libffi/bzip2/lzma support. The complete new environment imports torch `2.15.0+cu132` and passes `pip check`. To stay within the sandbox disk limit, 15 existing CUDA-library distributions with exactly matching locked versions are reused by symlink; all interpreter-specific Python wheels were installed for CPython 3.12. This local reuse is not represented as a clean-room dependency install; both fresh hosted runners install the lock normally.

The statistical catalog imported from main is now explicitly read-only and reflects actual TCP test-panel selections. The original checkboxes were unbound controls that could misleadingly suggest a test was disabled without changing the audited model. The confidence tooltip discloses method assumptions and autocorrelation/shift limitations rather than promising unconditional coverage. Added regression assertions cover both corrections.

### Fresh product evidence on the final product source

Source `d8489365c5ff26649543959abf005c1282aab5f3` generated fresh native SQLite/ODBC evidence (4/4 generated-profile checks and 10/10 read-only/security checks), Phase 7 product evidence (11 manifest hashes, all UQ tolerance checks true), and Phase 8 reports. Four renderers agree on 20 artifact-bound metrics; the Executive PDF has 9 pages and deterministic bytes; audit verification reproduces 20 metrics from 8 files. The Phase 8 manifest has 12 verified hashes. No legacy 3.11 report is reused as migration evidence.

Report ID: `3e033af6b9c0f263ac91c3a3040ed1fb7084ae91efcb8d54e90a54939453fc7f`. Audit archive SHA-256: `c8e428b394f1ac65633548073841cb1f64a4221a4702a29a26f6170da491d390`.

Static verification on Python 3.12 passed all 11 checks. Source binding covers 293 tracked non-document/evidence files with zero drift; the four mandatory baseline tests, LICENSE and `artifacts/review_baseline/` remain unchanged against pre-integration source. Raw results are in `artifacts/remediation/python312/`.

### Local full-suite teardown finding (not hidden as a pass)

The first coverage run reported **498 passed**, **0 skipped**, **85.66% core line coverage** in 2156.54 seconds and wrote complete JUnit/coverage XML. However, the custom sandbox Qt runtime then emitted `QObject: shared QObject was deleted directly` and segfaulted during interpreter teardown (**process exit 139**). This is **not accepted as a clean run**. A separate full GUI recheck passed all 17 cases in 41.03 seconds with exit 0. The complete suite is being rerun without coverage instrumentation to verify clean process exit; the original failed-process evidence is retained. Hosted Windows/Linux CI remains the independent native-runtime acceptance requirement.

### Confirmed native teardown defect and correction

The initial Linux hosted job `132761174519` (run `43697496842`, source `d7fc8f9`) also exited 139 after the full suite, with no failing test case. This is not solely a custom-sandbox issue. Windows reached packaging on the subsequent source. The first crash is retained as failed evidence, not acceptance.

Inspection confirmed a Qt ownership/lifetime defect: main gave QMdiSubWindow objects the top-level `Window` type, and every MainWindow added a global QtLogHandler whose signal retained a closed window's widget graph. Closed main windows had no delete-on-close policy or logging teardown. The correction uses the `SubWindow` type while retaining maximize/minimize controls, parents the emitter to its window, removes/disconnects the global logging handler on accepted close, stops page timers, disconnects the state callback, and requests normal Qt delete-on-close. Regression assertions enforce handler-count restoration, timer shutdown and actual deferred C++ window deletion in both neutral/energy cases. No test is removed, skipped or weakened. Fresh full-suite and two-OS CI results must validate this correction before acceptance.

### Residual test-harness ownership and stronger isolation

The first production ownership fix is not sufficient on its own: source `6511457` again reported 498 passing local tests (713.62 seconds) and then exited 139. This failed process is retained in `pytest-fixed.txt`/XML. Conversely, hosted run `43697653541` on the preceding `d848936` completed successfully on both Windows and Linux, so the symptom is intermittent, not a deterministic assertion failure.

A focused diagnostic identified dozens of still-live top-level Qt objects after the legacy GUI suite, including test-only `Main`, `_StubMain`, ResultsPage and MonitorPage objects. Those tests use qapp and close(), not qtbot.addWidget(); close() only hides a QWidget, leaving native deletion until global interpreter teardown. A new `tests/gui/conftest.py` isolation fixture closes and schedules deletion of test-owned root windows, drains DeferredDelete while QApplication still exists, runs garbage collection, and **asserts every captured root really became invalid**. It does not alter existing tests, skip execution, suppress exceptions, relax assertions or intercept process exits. Production MDI/logging ownership fixes remain independently enforced by their regressions. Fresh full-suite and two-OS runs are still required on this strengthened teardown contract.

Direct standard pytest invocation of the strengthened GUI suite passed 17/17 cases in 40.03 seconds with exit 0 (`gui-isolation.txt`/XML). An earlier ad-hoc `pytest.main()` diagnostic wrapper is not acceptance evidence: it lacked a `__main__` guard for multiprocessing spawn and produced a worker-handshake timeout. That wrapper is not shipped or used by CI. Standard `python -m pytest` remains mandatory.

### Clean local acceptance and unchanged-source CI revalidation

With strict native-widget teardown in place, the full standard suite on `f0a6981d10cbab5ebd4365cfbe86b5886ea1c4bb` completed **498 passed, 0 failed, 0 skipped, exit 0 in 646.40 seconds** (`pytest-isolation.txt`/XML). No process-exit override or warning suppression is used.

Run `43702773093` subsequently recorded a Windows Gate G1 timing failure in the unchanged `test_db_matrix_unreachable_postgres_probe_audits_and_warns`: total initialization took 5.297 seconds against its existing 5.0-second limit. The original test and limit remain unchanged, and its official annotation is retained. This is a distinct timing finding, not evidence that the Qt teardown fix failed. Direct job rerun/cancel is denied by the GitHub integration's Actions write permission (HTTP 403), although push and check reads work. Therefore empty commit `b08e7e8a3a12e1e83375818ad091e0541d75875b` revalidates the **identical source tree** without changing code, tests, budgets, coverage or workflow steps. Final native Windows/Linux acceptance remains pending that revalidation. Timing sensitivity must not be hidden if the retry passes.

The empty commit did not trigger Actions because the existing push path filters see no changed files. A root README setup clarification therefore triggers the retry, without changing implementation, dependencies, tests or workflow steps. The local 498-pass source remains identical for all executable/test files; the README now links this evidence and explains recreation of old virtual environments and native Qt component pins.
