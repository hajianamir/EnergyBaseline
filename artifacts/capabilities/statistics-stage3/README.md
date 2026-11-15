# Stage 3 verified bounded statistical capabilities

## Source and scope

Final tested product/test/workflow source: **`9dc70d5ef939dfc54dd0fd5dcace8479b9744460`**, introduced in `c3706a0` and hardened at primitive boundaries in `9dc70d5`. Final-source workflow: https://github.com/hajianamir/EnergyBaseline/actions/runs/43749525830 — **completed success on both Windows and Linux**.

This stage adds **27 preregistered statistical method identifiers**, exact-plan TCP digest registration, input-bound accepted VSG / locked TCP authorization, a native allowlisted spawn-worker and CLI interface, and the twentieth exploratory operation, `business-validation`. See `docs/PREREGISTERED_STATISTICS.md` for definitions, assumptions, execution instructions and exclusions. This is a bounded expansion of C003/C005 and C008–C015, **not completion of the whole capability backlog**.

## Actual final-source local execution (E1)

- `focused.log` / `focused.xml`: **202 passed in 58.64 seconds**, process exit **0**, no failures/errors/skips. Includes actual spawned-worker/native Qt tests, all statistical method identifiers, all 26 workbench manifest examples, gate/input/plan/primitive-boundary rejection, posterior/optimization/sampling oracles, resource bounds, privacy and persistence records.
- `focused-coverage.log` / `.xml` and `capability-coverage.xml`: independently **202 passed in 42.33 seconds**, exit **0**. Statistics plus workbench coverage **89.51%**, **1,511 / 1,688 statements**, passing an 80% gate. This is **capability-package coverage, not whole-project coverage**.
- `gui.log` / `gui.xml` and `gui-coverage.xml`: complete GUI directory, **32 passed in 181.62 seconds**, exit **0**, no failures/errors/skips. GUI coverage **72.09%**, passing the unchanged 56% gate. New statistical panel coverage **95%**. Actual native offscreen Qt and spawned worker PIDs are exercised; no fabricated worker results substitute for execution.
- `full-direct.log` / `full-direct.xml`: complete unchanged regression selection, **754 passed in 694.42 seconds**, no failures/errors/skips. `full-direct-exit.json` records actual pytest return code **0 after interpreter teardown**, total subprocess time 698.63 seconds and exact command. This full local run was **without coverage instrumentation**. Expanded whole-suite coverage gates were separately executed and passed in actual final-source CI on **both OSes**; no numerical local whole-project coverage percentage is claimed.
- `cli-examples.json`, `examples/*.json` and corresponding stderr files: **six actual separate final-source CLI subprocesses**, all exit **0**, stdout equals written JSON. The five statistical plans cover every method identifier; the sixth demonstrates business policies. Output hashes and source commit are recorded.
- `numerical-oracles.py`, `.json`, `.log`: reproducible supplementary independent linear-algebra/cell-mean checks for balanced factorial F/probabilities/partial eta squared, repeated-measures F, ANCOVA partial F, two-group MANOVA via Hotelling T2/determinants, and tied-rank Dunn variance. All seven comparison groups pass. These supplement, not replace, pytest and two-OS CI.
- `static-checks.log` / `static-exit.json`: Python runtime contract, unchanged locked-dependency consistency, Ruff lint/format, Pyflakes, original baseline test integrity, web-free, environment-reader, no-QThread-compute and requirements-matrix checks all exit **0**. Historical matrix remains 251 IDs / 188 done; it is not the broader capability backlog.
- `source-binding.json`: **374 tracked non-documentation/evidence files**, Git clean-filter blobs checked against the tested commit, **zero drift**. Documentation/evidence follow-up does not change these bound files.

## Actual Windows/Linux verification (E3)

Workflow **43749525830**, tested source **9dc70d5ef939dfc54dd0fd5dcace8479b9744460**, completed **success**. Windows job **132920145642** and Linux job **132920145882** both completed successfully, including the expanded full-suite/coverage and complete GUI/coverage steps under unchanged thresholds. Coverage scope now includes `energybaseline/statistics` in addition to the existing packages. Applicable Windows packaging/signing, native adapter, audit and SBOM checks passed. Linux correctly skips Windows-only packaging rather than pretending to execute it.

`hosted-run.json`, `hosted-windows-job.json` and `hosted-linux-job.json` are actual completed GitHub API responses, not mocks, earlier-source substitutions or hand-authored success records. The current job-log request failed under sandbox/integration access policy; `hosted-log-access.json` records that limitation without retaining signed redirect credentials. **No raw hosted log is claimed**. Actual API receipts and raw local execution logs/JUnit are retained.

## Environment, caught failures and limits

`environment.json` records genuine source-built **CPython 3.12.16**, unchanged hash-locked dependencies, actual native offscreen Qt and SQLite ODBC. Qt/ODBC native libraries use the repository's test-only local bootstrap; this is not a claim of production Linux deployment certification.

The initial native attempt without the Qt library path could not import `libGL.so.1` and was not counted as passing. A genuine numerical oracle test also caught a current SciPy string-dispatch normal-KS incompatibility with location/scale positional arguments. The implementation now supplies an explicit normal CDF callable with named location/scale; the same statistic/probability requirements remain tested. No historical assertion was weakened.

An initial full-regression attempt encountered compiler dependency sources in the temporary `.cache` tree scanned by the unchanged web-free guard. Temporary source trees were moved under ignored `.venv/native-sources`; runtime prefixes stayed unchanged. The guard was not weakened and the interrupted attempt was not counted as a regression. Resource review added repeated-measures subject/matrix limits, preallocation chi-square cardinality checks and total expression-node work limits before final-source acceptance.

A first final-source local full-suite attempt also omitted the built ODBC prefix from `LD_LIBRARY_PATH`, causing six existing SQL-source tests to fail importing `libodbc.so.2`. `native-odbc-failure.json` records the actual diagnostic identifiers/import errors without exporting source-level synthetic credential fixtures. `full-missing-odbc-interrupted.log` is the interrupted failing progress transcript, **not** a completed/passing suite. All eight selected unchanged checks then passed with both genuine runtime prefixes (`native-odbc-environment-check.log`), and the complete direct regression passed as recorded above. No product/test change was needed for that environment error.

Final security review bound direct primitive-registry calls to live gates plus exact declared method, columns, parameters and confidence. Earlier-source in-flight execution/CI is superseded, never substituted for final-source evidence. A request to cancel the superseded workflow lacked integration permission; its outcome is irrelevant to this final-source acceptance.

No baseline/report authorization, automatic refit, target imputation, renderer, dependency hash-lock or mandatory baseline test contract is weakened. The sole existing workbench test assertion changed from 19 to 20 registered operations to reflect the real added operation; it still asserts exact registry cardinality and uniqueness.

## Research and remaining program

`research.json` records **ten actual primary-source reads** with Git blob/content hashes: ordinary KS versus Lilliefors, design assumptions, tie-corrected Dunn, alpha-dependent two-stage FDR, prospective power, Gaussian marginal likelihood and classical versus modern MCMC diagnostics. This is research, **not** ArviZ/PyMC/Stan or external validation-framework integration. No dependency was added and no third-party source is vendored.

Custom linear/normal Bayesian families do not establish arbitrary probabilistic programs, general backend integration, hierarchical inference or universal convergence. The business engine is not Pandera/Great Expectations. Other per-row exclusions and pending catalog families remain incomplete. Hosted CI does not establish production signing, physical-device/reboot behavior, remote database TLS certification, general causal identification, fairness legality or distribution-free time-series validity.
