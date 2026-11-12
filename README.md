# EnergyBaseline v3 — offline desktop energy-baseline analytics

Python 3.11 · PySide6 (native Qt GUI) · SQLite · scikit-learn / statsmodels / XGBoost / LightGBM / CatBoost / PyTorch · Windows 10/11 x64.
No browser, no web server, no localhost, no Docker/Redis/Celery.

```
install.bat            # once: venv + requirements + migrations + EnergyBaseline.exe + .lnk (developer step)
EnergyBaseline.exe     # end users: double-click
start.bat / stop.bat   # developer launcher / stop
python -m energybaseline
python -m pytest       # backend test-suite (config/DB fallback, temporal, pipeline, monitoring, reports)
```

## Layout
```
energybaseline/
  config/settings.py   the ONLY reader of DATABASE_URL & friends; get_database_url(); SQLite fallback; ensure_dirs()
  db/                  SQLAlchemy 2 models, engine (zero-fail), self-test, programmatic Alembic upgrade
  core/                schema (all settings, AUTO), auto (resolution log), temporal, ingest, quality, features, eda, mining,
                       models + nn, scoring, pipeline, store, monitor, sqlsource, secrets, report (+template)
  gui/                 main window (tabbed MDI + docks), setup, analysis, results, selection, monitor, SQL dialog, status, Advanced dialog
  launcher.py          exe entry: mkdir data, export DATABASE_URL=sqlite:///./data/app.db, shortcuts, start GUI
alembic/ alembic.ini   migration 0001 (uses the same resolver in env.py)
samples/ examples/ docs/ scripts/ tests/ assets/
```

## Design decisions / documented assumptions
* **Validation**: all models compete on identical, purge-gapped, time-aware folds (blocked K-fold for < 2 years of data, forward-chaining otherwise). Hyper-parameters (Optuna TPE) are tuned *inside each training fold* (nested). Metrics are out-of-fold; no degrees-of-freedom correction.
* **Uncertainty** = empirical 2.5/97.5 % quantiles of out-of-fold residuals (valid for every model family).
* **Weekly** acceptance thresholds (20 % / 7 %) interpolate ASHRAE 14; R² gates follow IPMVP guidance.
* **Leakage rule per target**: other TARGET columns are never predictors; energy-like columns with |r| ≥ 0.99 are excluded; non-energy columns that strongly correlate are kept with a warning.
* **Regime labels** from clustering are *not* model inputs (derived from the target).
* TFT / PatchTST / Informer are optional in the specification and are listed as "not implemented" in the rejected register; Prophet, UMAP, SHAP, CatBoost, mlxtend are used when installed and reported as skipped otherwise.
* PDF: WeasyPrint when its GTK runtime exists, else Qt's text engine, else a dependency-free matplotlib writer.
* `EnergyBaseline.exe` must be built on Windows (`install.bat` → `scripts/build_exe.py`); the `.lnk` is created by the build and again on first launch.
