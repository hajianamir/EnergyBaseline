# Stage 2 executable workbench evidence

## Source and scope

Implementation and coverage-workflow source: `5e571cc96404cd3e9b4a70a59fab66b11fa393ad` (implementation introduced in `2c52a29`). Current final-source workflow: https://github.com/hajianamir/EnergyBaseline/actions/runs/43737625559

This stage adds **19 bounded offline operations**, **four additional integrated TCP corrections**, native spawned-worker GUI execution and **25 executable synthetic examples**. See `docs/EXPLORATORY_WORKBENCH.md` for mathematical definitions, exact schema and explicit exclusions. It does **not** complete all 40 original backlog rows or promote these exploratory models into the accepted baseline lifecycle. Dependency lock, default alpha/corrections, mandatory tests, baseline/report authorization and renderer contracts are not weakened. The CI coverage scope is expanded to include the new workbench package.

## Final-source local execution

- `focused.log` / `focused.xml`: **120 passed in 13.44 seconds**, exit **0**, including three actual Qt GUI tests and all 25 examples through the CLI handler. No mocks substitute for the worker/native execution. No skips.
- `gui-and-gates.log` / `.xml`: **39 passed in 142.84 seconds**, exit **0**, complete GUI directory plus original Phase 3 cache/what-if and fuzz gates. GUI coverage **71.65%**, unchanged gate >=56%.
- `gui.log` / `.xml`: **28 passed in 116.98 seconds**, exit **0**, complete GUI directory independently; coverage **71.68%**, unchanged gate >=56%. New GUI module coverage **91%**.
- `full-suite.log` / `.xml`, `coverage.xml`, `coverage-gate.log`: **672 passed in 2527.26 seconds**, no failures/errors/skips. Expanded coverage **88.51%** (15680 / 17715 statements); both unchanged 60% and 80% gates passed. The coverage-wrapper process exited 0. A separate direct pytest subprocess run also completed: **672 passed in 695.44 seconds**, with actual pytest process return code **0 after interpreter teardown**, recorded in `full-direct-exit.json` and `full-direct.{log,xml}` without relying on any shell pipeline. No earlier full-suite result is substituted.
- `static-checks.log`: Python 3.12.12, pip dependency consistency, Ruff lint/format (124 files), Pyflakes, original baseline test integrity, web-free, environment-reader, no-QThread-compute and requirements-matrix checks all exit **0**. Matrix: 251 IDs, 188 historical done; this historical compliance matrix is not the broader capability backlog.
- `example-results.json` and `examples/*.json`: **25 actual separate CLI subprocess executions**, all exit **0** with stdout equal to written JSON. Raw outputs and hashes are captured on final source, not hand-authored expected results.
- `environment.json`: actual local runtime/dependency/native-platform snapshot. Genuine source-built Python **3.12.12**, complete unchanged hash-locked dependencies; genuine native offscreen Qt and SQLite ODBC via the repository's test-only fallback bootstrap. These are local test-environment facts, not production Linux adapter/deployment certification. Build/log timestamps reflect environment clocks and are not rewritten.
- `source-binding.json`: **356 tracked non-doc/evidence files checked, zero drift**, using Git clean-filter blobs (including configured line-ending normalization) against the tested source. Documentation/evidence follow-up must not modify these bound files.

## Regression caught, not waived

The draft extension increased the default sensitivity sweep from 30 to 54 scenarios because it iterated the expanded enum. The unchanged mandatory Phase 3 test exposed that incompatibility. The default sweep now explicitly retains the historical five mandatory corrections / 30 scenarios. Extended corrections are opt-in; new tests prove cached extended what-if sweeps have **zero inferential engine calls**. The original test was neither edited nor weakened. The first draft documentation code block also required formatting; final static checks pass after formatting.

## Actual Windows/Linux verification

Final-source workflow **SUCCESS**: [43737625559](https://github.com/hajianamir/EnergyBaseline/actions/runs/43737625559), source `5e571cc96404cd3e9b4a70a59fab66b11fa393ad`. **Windows and Linux both completed successfully**, including full-suite/coverage and GUI/coverage steps. Windows packaging/signing smoke and applicable native/audit/SBOM steps completed under the unchanged thresholds. Linux job `132883616292`; Windows job `132883616476`.

`hosted-run.json`, `hosted-linux-job.json` and `hosted-windows-job.json` are actual completed GitHub API responses. These are not mocks or substituted older-source results. The sandbox cannot download the job-log redirect from the non-allowlisted artifact-storage host: `hosted-log-access.json` records that limitation. **No hosted raw log is claimed**; raw local logs/JUnit and actual two-OS success receipts are preserved. No signed temporary redirect URL is stored.

CI can certify only its executed scopes. It does not establish production signing, physical desktop/device behavior, remote database TLS, reboot behavior, general causal identification, fairness legality, adversarial certification or distribution-free time-series guarantees.

## Research

`research.json` records actual primary GitHub API source reads with content hashes. Source review guided temporal target-encoding, in-fold selection, censored-event handling, fairness denominators, solver-status and multiplicity contracts. Fairlearn/SimPy research is **not** an installation/integration claim. No new third-party source is vendored and no dependency is added.
