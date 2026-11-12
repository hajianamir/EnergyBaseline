# EnergyBaseline v6 — offline desktop energy-baseline analytics

Python-only · PySide6 · offline · no web server · no browser.

Python 3.11.9 · PySide6 (native Qt GUI) · SQLite · scikit-learn / statsmodels / arch / MAPIE / XGBoost / LightGBM / CatBoost / PyTorch · ReportLab · Windows 10 22H2 / Windows 11 x64.

```
install.bat            # once: venv + hashed lock requirements + migrations + EnergyBaseline.exe + .lnk (developer step)
EnergyBaseline.exe     # end users: double-click
start.bat / stop.bat   # developer launcher / stop
python -m energybaseline
python -m pytest -q    # automated test suite (DB fallback, temporal, pipeline, monitoring, reports, gates, GUI smoke)
```

## Layout
```
energybaseline/
  config/settings.py   the ONLY reader of DATABASE_URL & friends; get_database_url(); SQLite fallback; ensure_dirs()
  db/                  SQLAlchemy 2 models, engine (zero-fail), self-test, programmatic Alembic upgrade
  core/                schema (all settings, AUTO), auto (resolution log), temporal, ingest, quality, features, eda, mining,
                       models + nn, scoring, pipeline, store, monitor, sqlsource, secrets, report (+template)
  gui/                 main window (tabbed MDI + docks), setup, analysis, results, selection, monitor, SQL dialog, status, Advanced dialog
  launcher.py          exe entry: mkdir data, shortcuts, start GUI
alembic/ alembic.ini   migration 0001 (uses the same resolver in env.py)
samples/ examples/ docs/ scripts/ tests/ artifacts/
```

## Design decisions / documented assumptions
* **Validation**: all models compete on identical, purge-gapped, time-aware folds (blocked K-fold for < 2 years of data, forward-chaining otherwise). Hyper-parameters (Optuna TPE) are tuned *inside each training fold* (nested). Metrics are out-of-fold; no degrees-of-freedom correction.
* **Uncertainty**: interim empirical 2.5/97.5 % quantiles of out-of-fold residuals (MAPIE conformal UQ scheduled for Phase 7).
* **Weekly** acceptance thresholds (20 % / 7 %) interpolate ASHRAE 14; R² gates follow IPMVP guidance.
* **Leakage rule per target**: other TARGET columns are never predictors; energy-like columns with |r| ≥ 0.99 are excluded; non-energy columns that strongly correlate are kept with a warning.
* **Regime labels** from clustering are *not* model inputs (derived from the target).
* TFT / PatchTST / Informer are optional in the specification and are listed as "not implemented" in the rejected register; Prophet, UMAP, SHAP, CatBoost, mlxtend are used when installed and reported as skipped otherwise.
* **PDF rendering**: ReportLab is the ratified single PDF engine (target architecture in Phase 8; interim fallback uses Qt's text engine or matplotlib without external native runtimes).
* `EnergyBaseline.exe` must be built on Windows (`install.bat` → `scripts/build_exe.py`); the `.lnk` is created by the build and again on first launch.
* Support matrix, security threat model, third-party notices, and phase governance live in `docs/SUPPORT_MATRIX.md`, `SECURITY.md`, `THIRD_PARTY_NOTICES.md`, `docs/EXECUTION_PLAN.md`, and `docs/DECISIONS.md`.
