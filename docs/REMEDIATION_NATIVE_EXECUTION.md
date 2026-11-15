# Native execution and final-boundary remediation

> Subsequent native ODBC enforcement and raw-evidence execution:
> `docs/REMEDIATION_CLOSURE_EXECUTION.md`. Results below belong to this earlier
> snapshot, not the later ODBC changes.

This checkpoint supersedes the missing-library limitation of
`REMEDIATION_REMAINING_IMPLEMENTATION.md` for **Linux offscreen Qt only**.
It is not a release, real-display, two-OS or secure credential-store certificate.
All original tests, LICENSE and `artifacts/review_baseline/` remain unchanged.

## Implemented changes

- Schema population now synchronizes the schema table, legacy target checkboxes,
  application targets and timestamp state before building VSG/TCP. Neutral schema
  suggestions remain neutral: the first eligible numeric column may be suggested
  as a target, without recognizing energy names or inventing energy units.
  Editing target roles under neutral policy uses quantity `quality`, not `flow`.
- Actual native tests exercise a signed manufacturing dataset through MainWindow,
  schema collection, accepted VSG, preregistration lock, AnalysisPage dispatch,
  spawned analysis worker, raw-fold evaluation, gated inference and SQLite run
  persistence. The small synthetic input is a behavioral proof, not production
  model accuracy or performance evidence.
- Compatibility preprocessing and core feature selection no longer run hidden
  LassoCV after learning scaling from the complete enclosing training fold.
  Lasso uses an explicit scale-relative penalty (`0.01 * training target standard
  deviation`); tuning belongs to independently prepared raw inner folds.
  This can legitimately change selected features, scores and shortlist ordering.
- Both compatibility preprocessing entry points reject zero, negative, boolean,
  fractional and nonfinite lag/window settings instead of silently truncating or
  dropping them. Rolling-origin training may receive the actual causal history;
  statistics and selection still use only training observations. History must
  preserve those observations and their order. Training audit fingerprints now
  cover complete predictor values, target values and any supplied history.
- Compatibility energy metrics retain historical numeric policy-limit fields,
  but mark weekly/daily/subhourly/unknown intervals as not recognized standard
  intervals. Those intervals cannot claim `meets_ashrae14`. Undefined R-squared
  cannot pass the compliance predicate. Additive policy metadata is version 2;
  this does not introduce energy standards into neutral SDK/product workflows.
- Opting out of SQL credential persistence also keeps saved SQL text in session
  memory only, because SQL literals may contain secrets. Passwords, ODBC strings
  and saved queries survive current-session profile reload but not restart.
- The full T1–T14 catalog now requires accepted input-bound VSG and explicitly
  locked TCP, not acceptance alone or a missing panel. Canonical orchestration
  provides the original authorized source frame when the catalog consumes
  temporally normalized/evaluable observations. CLI `test-catalog` explicitly
  preregisters before execution. Low-level statistical primitives remain testable
  algorithms, not independent application authorization entry points.

## Genuine native runtime, reproducibility and scope

The isolated build uses actual libglvnd, Expat and DBus source builds, actual
system X11/Xext libraries and the real xkbcommon library distributed in an
OpenCV wheel. OpenCV is not installed as a product dependency. QtWidgets and its
QApplication are native PySide6; no mocked substitutes are used for this proof.
`offscreen` is a real Qt platform plugin, not a physical display/DPI validation.

Prefer the distribution packages already specified by the CI workflow. When
those packages cannot be installed, run:

```sh
.venv/bin/python scripts/evidence/bootstrap_native_qt.py
LD_LIBRARY_PATH="$PWD/.cache/native-rebuild/prefix/lib" \
  QT_QPA_PLATFORM=offscreen EB_NONINTERACTIVE=1 \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 \
  .venv/bin/python -m pytest -q -p pytestqt.plugin --tb=short
```

The fallback is Linux x86-64 and requires cc/ld, Python 3.11 and system X11/Xext
runtime libraries. Downloads are SHA-256 checked and source commits verified.
The old DBus client source is test-only, not an approved deployed bus service or
security update recommendation. All runtime binaries and build tools stay in
ignored `.cache/`; they are not source deliverables. Source provenance and logs
are retained in `artifacts/remediation/native/`.

## Evidence and remaining gates

- Initial original GUI suite: `pytest-gui-initial.txt`, **8 passed, 3 failed**.
  Two real failures traced to the schema/control synchronization defect.
- Added native regression module: **4 passed in 12.54 seconds** before the full
  final run, including the actual desktop-to-spawned-worker path. Its separate
  surrogate-label fixtures are explicitly UI-only, not model-fitting evidence.
- Added compatibility temporal regressions: **17 passed in 1.51 seconds**.
- `static-checks.txt`: 11 commands, zero failures; `source-manifest.json` identifies
  207 product/test/evidence/configuration files for this checkpoint.
- `pytest-full-exploratory-stopped.txt` is an intentionally stopped preliminary
  run before the final boundary fixes. It is not an acceptance result.
- `pytest-focused.txt`: **127 passed in 54.24 seconds** across seven remediation
  suites, including the final catalog and SQL boundaries.
- `bootstrap-verification.txt`: isolated hash/commit-verified rebuild completed
  with exit code 0 and instantiated the actual offscreen QApplication. The GUI
  suite below used this newly rebuilt runtime, not the original local build.
- `pytest-gui-final.txt` and `.xml`: **14 passed, 1 failed in 59.70 seconds**,
  15 native GUI tests collected, zero skips. The only failure is the unchanged
  original hardcoded surrogate-fidelity assertion described below; actual card
  fidelity is `0.979`, not its injected fixture's `0.941`.
- `source-verification.json`: all 207 recorded files rechecked with no drift.
- `pytest-full.txt` and `.xml`: **428 passed, 1 failed in 706.27 seconds**;
  429 collected, zero errors and zero skips. All 414 non-GUI cases passed;
  native GUI contributed 14 passes and the same single hardcoded-fidelity
  assertion failure. This is an exit-code-1 regression, not a green acceptance
  run. Source hashes were rechecked after completion with no drift.
- `summary.json` records the counts and remaining gates; `evidence-sha256.json`
  binds retained logs, provenance and source manifests. Older 395/119-pass
  manifests certify only their historical source snapshot, not this later tree.

A known original GUI assertion hardcodes surrogate fidelity `0.941`. It injects
its artificial candidate only when the shortlist contains no actual nonparametric
candidate. On this evaluator, the real shortlist already contains a nonparametric
candidate with a different computed fidelity. Rendering the artifact's actual
fidelity is the correct behavior; the original test is retained unchanged and is
not xfailed, skipped or masked. Added native tests verify rendering two distinct
artifact fidelity values rather than a hardcoded fitting result. This remains a
red acceptance gate unless the original test contract is formally revised.

Still required externally: actual Windows execution and two-OS E3; original raw
E1 acceptance requirements; real secure OS credential-store durability and failure
checks; real MySQL/SQL Server/ODBC deployment validation. No CI preparation,
synthetic dataset, offscreen Linux run or library bootstrap replaces those gates.
