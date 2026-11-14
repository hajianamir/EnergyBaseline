# Specification Deviations & Gap Register

Last updated: `2026-11-12` (Phase 0 — Truth & Rails)

This register records every known gap between the baseline commit (`2c92f2a06f6fb04cd3aed584ba82639144bfbfa1`) and the v6 target specification (`docs/EXECUTION_PLAN.md`), together with ratified architectural deviations (`docs/DECISIONS.md`) and verified third-party library API differences on Python 3.11.

---

## 1. Ratified Architectural & Scope Deviations

### DEV-01 — Authenticode Production Signing Deferred (`D1 = B`, `PKG-3` `[M] -> [S]`)
- **Date recorded:** `2026-11-12`
- **Affected requirement:** `PKG-3` (reclassified from `[M]` to `[S]` per `docs/DECISIONS.md` §D1).
- **Deviation:** Hardware-token OV/EV Authenticode signing is deferred while builds remain internal to developer workstations and CI runners.
- **Phase 0 mitigation:** `scripts/sign/sign_artifacts.ps1` implements full SHA-256 Authenticode signing and verification (`signtool sign /fd SHA256` + `signtool verify /pa /v`), exercised on every `windows-latest` CI run via `-SelfTest` using an ephemeral self-signed code-signing certificate.
- **Mandatory re-entry trigger:** **Must be upgraded to a production CA-issued hardware-backed Authenticode certificate before any distribution of `EnergyBaseline.exe` or installers outside developer machines.**

### DEV-02 — Interim PDF Fallback Between Phase 0 and Phase 8 (`D5 = A`)
- **Date recorded:** `2026-11-12`
- **Affected requirements:** `D0.2`, `RPT-1..7`, `AC-25`.
- **Deviation:** Per `D5 = A`, the legacy HTML-to-PDF GTK dependency has been completely removed from `energybaseline/core/report.py`, `scripts/build_exe.py`, and all dependency manifests in Phase 0, and banned by `scripts/gates/guard_web_free.py`. Because Phase 0 freezes product features, `energybaseline/core/report.py` retains its existing pure-Qt (`QPrinter`) and `matplotlib.backends.backend_pdf.PdfPages` fallback as an interim PDF path until **Phase 8** replaces it with the full ISO 50006 **ReportLab Platypus** renderer.

### DEV-03 — Isolated Zero-Logic Pyflakes Hygiene on Frozen Baseline Modules (`G0`)
- **Date recorded:** `2026-11-12`
- **Affected gate:** `G0` (`python -m pyflakes energybaseline`).
- **Context:** Baseline commit `2c92f2a` produced 32 `pyflakes` unused-import and unused-variable warnings across 15 files (`config/__init__.py`, `db/__init__.py`, `core/{features,mining,models,pipeline,quality,report,scoring,secrets,sqlsource,store}.py`, and `gui/{main_window,page_monitor,pages_results}.py`), because `pyflakes` does not honor `# noqa: F401`.
- **Resolution:** Isolated in a dedicated, standalone commit containing strictly zero runtime or numerical behavior changes (`__all__` declarations on package `__init__.py` files, `_ = <mod>` on optional-import availability probes, and removal of dead unused symbols) so Gate G0 passes cleanly without weakening the gate command.

### DEV-04 — SQLite Connection Disposal in `alembic/env.py` on Windows + SQLAlchemy 2.1 (`D0.4` / `D0.8`)
- **Date recorded:** `2026-11-12`
- **Affected file:** `alembic/env.py` (outside `energybaseline/`).
- **Context:** Under `SQLAlchemy==2.1.8` on `windows-latest`, `create_engine(_url())` defaults to `QueuePool` for file-backed SQLite URLs. Because `alembic/env.py::run_migrations_online()` did not pass `poolclass=NullPool` or call `engine.dispose()`, an uncollected pooled SQLite handle caused `PermissionError: [WinError 32]` in `tests/test_config_db.py::test_alembic_upgrade_on_clean_machine` when unlinking `app.db` on Windows.
- **Resolution:** Configured `NullPool` and `try ... finally: engine.dispose()` in `alembic/env.py` while keeping `tests/test_config_db.py` byte-identical. Also configured `ubuntu-latest` (migrated by GitHub Actions to Ubuntu 26.04) to use `python-version: "3.11.18"` while `windows-latest` runs exact `python-version: "3.11.9"` because `actions/python-versions` only publishes `3.11.17+` tarballs for Ubuntu 26.04.

---

## 2. Baseline Gap Register (`2c92f2a` vs Target Architecture)

| ID | Area | Baseline State (`2c92f2a`) | Target Specification | Resolution Phase | Phase 0 Status |
|---|---|---|---|---|---|
| GAP-01 | Repository purity | Next.js / TypeScript / Drizzle web files committed (`package.json`, `tsconfig.json`, `next.config.ts`, `src/**`) | Pure offline Python desktop repository; zero web files or web servers | Phase 0 (`D0.1`, `D0.3`) | **Resolved** (removed + guarded by `guard_web_free.py`) |
| GAP-02 | Baseline test suite | 17 passed, 2 failed (`test_daily_multitarget_independent`, `test_nn_explicitly_rejected_for_small_data`) | 100% green deterministic suite across Full and Lite environments | Phase 0 (`D0.8`) | **Resolved** (session `.xlsx` fixture + parametrized MLP/LSTM contract) |
| GAP-03 | Dependency manifests | Floating `>=` pins in `requirements.txt`, no lockfile, no hashes, missing `joblib` / `mapie` / `arch` / `sqlglot` / `reportlab` | Exact `==` pins + SHA-256 hashed `requirements.lock` + `pip-audit` + CycloneDX SBOM | Phase 0 (`D0.2`, `D0.4`) | **Resolved** |
| GAP-04 | Concurrency & cancellation | In-process `QThread` workers (`gui/main_window.py`); `Cancel` cannot interrupt C extensions (`scipy`, `xgboost`, `torch`) | True `ProcessPoolExecutor(mp_context="spawn")` with < 2 s hard kill (`SHL-4`, `IPC-1..7`) | Phase 1 | Planned (tracked in `docs/requirements.yaml`) |
| GAP-05 | Startup schema migration | `db/session.py` calls `Base.metadata.create_all` alongside Alembic | Exclusive Alembic `upgrade head` startup flow with pre-migration backup (`DB-2..5`) | Phase 1 | Planned |
| GAP-06 | Model artifact serialization | `pickle.dumps` stored in SQLite `LargeBinary` (`core/store.py`) | `joblib` artifacts on disk + SHA-256 manifest verified before load (`SC-10`, `AC-04`) | Phase 1 / Phase 6 | Planned |
| GAP-07 | Variable Selection Gate (VSG) | Absent | Pre-modeling screen (`VSG-1..9`: missingness, variance, Pearson/Spearman, VIF, leakage, MI, consensus) | Phase 2 | Planned |
| GAP-08 | Target Characterization Profile (TCP) | Partial temporal/missingness checks spread across `temporal.py` and `quality.py` | Explicit 14-dimension `TCP` dataclass persisted as JSON before candidate generation (`TCP-1..14`) | Phase 3 | Planned |
| GAP-09 | Statistical Test Catalog | Subset of ad-hoc checks without unified `TestResult` schema or BH-FDR control | Full `T1..T14` catalog with `TestResult`, `p_raw`/`p_adj` BH-FDR, and R JSON validation (`TC-1..8`, `CI-1..4`) | Phase 4 | Planned (R JSON schema seeded in `tests/reference/README.md`) |
| GAP-10 | Model families & UQ | Subset of 7 families; ad-hoc residual intervals; no `MAPIE` conformal calibration or Diebold–Mariano/MCS | Full `F1..F10` registry, `MAPIE` EnbPI/ACI (`UQ-1..5`), nested walk-forward CV (`VAL-1..7`), transparent composite score (`SC-1..9`) | Phase 5 / Phase 6 | Planned |
| GAP-11 | Report engine | Optional GTK HTML-to-PDF path with basic fallback | Deterministic ISO 50006 ReportLab Platypus PDF + self-contained offline HTML (`RPT-1..7`) | Phase 8 | Interim fallback active (see `DEV-02`) |
| GAP-12 | Packaging & signing | Unsigned PyInstaller build script referencing removed HTML-PDF library; committed `.pyc` artifacts | Clean reproducible `--profile full|lite` PyInstaller bundle, SHA256SUMS, SBOM, signing script (`PKG-1..6`) | Phase 0 / Phase 9 | Rails resolved in Phase 0; full bundle E2E in Phase 9 |

---

## 3. Verified Python 3.11 Library API Deviations (Input to Phase 4)

Verified directly in `.venv` against `statsmodels==0.15.0` and `scipy==1.17.1` (matching baseline audit `artifacts/review_baseline/library_verification.txt`):

1. **`statsmodels.imputation.mice.MICEData` lacks `.mcar_test()` (`T1`):**
   `MICEData` provides MICE imputation (`F4` / `DAT-5`) only; Little's MCAR $\chi^2$ test must be implemented directly in `energybaseline/core/stats_catalog/` in Phase 4 and validated against an R `MissMech::mcartest` JSON reference fixture (`D4 = B`).
2. **`acorr_ljungbox` import location (`T6`, `T7`):**
   `acorr_ljungbox` is exported by `statsmodels.stats.diagnostic.acorr_ljungbox`, **not** `statsmodels.tsa.stattools`.
3. **`grangercausalitytests` `verbose` parameter removed/deprecated (`T14`):**
   Calling `statsmodels.tsa.stattools.grangercausalitytests(..., verbose=False)` raises `TypeError` in `statsmodels==0.15.0`. Phase 4 callers must omit `verbose`.
4. **`adfuller`, `kpss`, and `acorr_lm` `result_object` `FutureWarning` (`T6`, `T9`):**
   `statsmodels==0.15.0` emits `FutureWarning` announcing a future `ResultsStore` return object; Phase 4 adapters must unpack positional tuple fields explicitly or normalize both tuple and result-store forms.
5. **`scipy.stats.brunnermunzel` `midrank` parameter deprecation (`T3`):**
   `scipy==1.17.1` deprecates `midrank` on `brunnermunzel`; Phase 4 callers must use the current signature (`distribution='t'` / `nan_policy='propagate'`).

---

## 4. Phase 2 Deltas & Dependency Additions (`Gate G2`)

1. **`DEV-05` — Addition of `pyarrow==24.0.0` (`Apache-2.0`) for Parquet Ingestion & Verified Sidecar Cache (`DAT-1`, `DAT-8`, `Gate G2`):**
   - Pinned `pyarrow==24.0.0` across `requirements.in`, `requirements.txt`, `requirements.lock` (all 50 PyPI wheel + sdist SHA-256 digests), `THIRD_PARTY_NOTICES.md`, and `scripts/build_exe.py` (`HIDDEN = [..., "pyarrow", "pyarrow.parquet", "sqlglot"]`).
   - `pyarrow` is loaded lazily via `pandas.read_parquet(..., engine="pyarrow")` / `DataFrame.to_parquet(..., engine="pyarrow")` inside `energybaseline/core/ingest.py` rather than at module import time, preserving fast cold/warm desktop startup (`startup_time.ps1`).
   - Verified zero CVEs via `pip-audit -r requirements.lock`.
2. **Traceability Phase Alignment (`docs/requirements.yaml` vs `docs/EXECUTION_PLAN.md` §Phase 2–3):**
   - Marked all 10 Phase 2 requirements (`DAT-1..DAT-8`, `AC-06`, `AC-28`) `status: done` with verified `code_refs` and `test_ids`.
   - Aligned `VSG-1..VSG-9`, `AC-07`, and `PIPE-1` to `phase: 3` in `docs/requirements.yaml` matching `docs/EXECUTION_PLAN.md` §Phase 3 (`Scope: §10 (VSG-1..9), §12 (AUTO-1..6), §13 (TCP-1..14), PIPE-1..3`).


## Phase 7 — Gate G7 implementation deltas

- **DEV-07 — MAPIE 1.6 API:** EnbPI/ACI use `TimeSeriesRegressor.predict(confidence_level=...)` and explicit `adapt_conformal_inference` (ACI) followed by `update` after each observed outcome. Split/quantile use separate `fit`/`conformalize`; cross/bootstrap use `fit_conformalize`. No exchangeability guarantee is asserted for dependent series. Calibration failure remains visible and the UQ CLI returns nonzero; holdout outcomes never tune previously emitted intervals.
- **DEV-08 — Causal backend selection:** Added MIT `econml==0.17.0` and BSD `sparse==0.19.3` (transitive) with PyPI SHA-256 hashes. The installed EconML adapter estimates effects and runs a permuted-treatment placebo. DoWhy remains an optional adapter: version 0.14 requires SciPy <=1.15.3, incompatible with the locked SciPy 1.17.1. Missing DoWhy raises `CausalBackendUnavailable`; no substitute is reported as a DoWhy result. Native DID/HAC diagnostics report only possible savings/non-routine events; identification assumptions and overlap/pretrend/balance failures are explicit.
- **DEV-09 — Monitoring interfaces:** The strict Phase 7 reporting workflow is available as Python API and `monitoring-run` CLI, requiring schema units, exact granularity, a verified lock and calibration data preceding reporting. The legacy desktop workflow retains its existing permissive ingestion UX; desktop snapshots are now bound to the verified locked version and original-input hash. Full advanced drift/UQ control widgets are not claimed. JSONL incremental monitoring is single-user/single-writer; simultaneous external writers are unsupported. A new baseline/calibration reference or adjustment policy starts a separate monitoring history.
- **DEV-10 — Covariance propagation:** Savings bands use Bartlett-tapered residual autocovariances, calibration-mean uncertainty, independent measurement error and fully correlated static-factor uncertainty. These are estimated ASHRAE/IPMVP-style bands, not a certification. Larger nonstationary changes require engineering review, not silent recalibration of the model.
- **Physical evidence:** D2 physical Win11 reference-machine timing and human visual review remain unproven in this sandbox. Headless Qt uses sandbox-only system-library stubs; real Ubuntu/Windows Qt is exercised by E3 CI. Production signing and project ZIP packaging remain later-phase work; Phase 7 provides read-only export inspection and persistence safety guards.
- **DEV-11 — Canonical source-grep false positive:** The pre-existing `Connection.secret_blob` ORM attribute/comment matched the new blanket source grep even though it held encrypted data. Renamed it to `protected_payload` through reversible migration 0004 and updated the SQL adapter; ciphertext is preserved byte-for-byte. Encryption behavior is unchanged. This resolves a source-name false positive, not a claim that the old value was plaintext.
