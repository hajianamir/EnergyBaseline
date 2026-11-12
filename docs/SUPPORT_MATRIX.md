# Support Matrix

Last updated: `2026-11-12` (Phase 0 — Truth & Rails)

## 1. Operating Systems & Architectures

| Platform | Architecture | Support Tier | Execution Mode |
|---|---|---|---|
| **Windows 11** (22H2, 23H2, 24H2) | `x86_64` (`AMD64`) | **Primary Production** | Native PySide6 GUI + PyInstaller `EnergyBaseline.exe` + source `.venv` |
| **Windows 10 22H2** | `x86_64` (`AMD64`) | **Primary Production** | Native PySide6 GUI + PyInstaller `EnergyBaseline.exe` + source `.venv` |
| **Ubuntu Linux** (`ubuntu-latest`, 22.04/24.04 LTS) | `x86_64` | **CI & Headless Verification** | Core numerical suite, static gates, and offscreen Qt smoke tests (`QT_QPA_PLATFORM=offscreen`) |
| macOS / 32-bit Windows / ARM64 Windows | — | Unsupported in v6.0 | Not targeted or tested |

## 2. Reference Benchmark & Visual Verification Hardware (`D2 = B + A`)

All performance budgets (`PER-1..6`, `AC-23`) and DPI/layout acceptance checks (`UX-11`) are measured against the ratified reference configuration:

- **CPU:** 8-core `x86_64` desktop/workstation processor
- **Memory:** 16 GB RAM
- **Storage:** Local NVMe SSD
- **Operating System:** Windows 11 23H2 (64-bit)
- **Display:** `1920×1080` @ `100%` DPI scaling (plus `125%` and `150%` DPI verification in Phase 8)
- **GPU:** None required (CPU-only execution by design)

## 3. Python Runtime (`S1`)

- **Pinned CI & Packaging Runtime:** **CPython `3.11.9` (64-bit)**
- **Supported Version Range:** `>=3.11,<3.12` (`pyproject.toml`)
- **Installation Manifest:** `requirements.lock` (`pip install --require-hashes -r requirements.lock`)

## 4. Packaging Profiles & PyTorch Wheel Specification (`D3 = A`)

| Profile | Build Command | `torch` Included | `MLP` (`F6`) | `LSTM` (`F6`) | Expected One-Folder Size |
|---|---|---|---|---|---|
| **Full (Default)** | `python scripts/build_exe.py --profile full` | Yes (`torch==2.15.0` CPU) | Enabled (`scikit-learn`) | Enabled (`PyTorch` CPU) | ~650–800 MB unpacked |
| **Lite (Optional)** | `python scripts/build_exe.py --profile lite` | Excluded (`--exclude-module torch`) | Enabled (`scikit-learn`) | Logged as `not evaluated` (`not installed`) | ~350–450 MB unpacked |

**Pinned Windows CPU PyTorch Wheel (verified in `requirements.lock`):**
- **Package:** `torch==2.15.0`
- **Wheel filename:** `torch-2.15.0-cp311-cp311-win_amd64.whl`
- **Wheel size:** `126.7 MB` (`126,729,036` bytes)
- **SHA-256:** `d2c5938f593a47cf6d151a16d130a3cc3f0c105df1b905a571efc00b01e38c1f`

## 5. Database & Ingestion Connectors

| Store / Source | Role | Driver / Engine | Support Status |
|---|---|---|---|
| **SQLite 3** (`%LOCALAPPDATA%\EnergyBaseline\data\energybaseline.db` or portable `./data/`) | Primary local application database | Built-in `sqlite3` + `SQLAlchemy==2.1.8` + `alembic==1.20.1` | Default, zero-config, offline |
| **CSV / TSV / Excel (`.xlsx`)** | File data ingestion | `pandas==3.1.1` + `openpyxl==3.1.5` | Built-in |
| **PostgreSQL** | Optional read-only EnPI/driver ingestion | `psycopg2-binary==2.9.11` | Supported |
| **MySQL / MariaDB** | Optional read-only EnPI/driver ingestion | `PyMySQL==1.1.2` | Supported |
| **Microsoft SQL Server** | Optional read-only EnPI/driver ingestion | `pyodbc==5.3.0` (requires Microsoft ODBC Driver 17/18 on host) | Supported |

## 6. Core Pinned Scientific & Desktop Stack

| Package | Exact Version | Purpose |
|---|---|---|
| `PySide6` | `6.9.3` | Native Qt 6 desktop UI |
| `numpy` | `2.4.6` | Numerical arrays and linear algebra |
| `pandas` | `3.1.1` | Time-series alignment and tabular processing |
| `scipy` | `1.17.1` | Hypothesis testing and optimization |
| `statsmodels` | `0.15.0` | Econometric, ARIMA/SARIMAX, and diagnostic statistics |
| `scikit-learn` | `1.9.1` | Regressors, preprocessing, MLP, and cross-validation |
| `xgboost` / `lightgbm` / `catboost` | `3.2.0` / `4.6.0` / `1.2.10` | Gradient-boosted tree baselines |
| `mapie` | `1.6.0` | Conformal prediction intervals (`EnbPI` / `ACI`) |
| `arch` | `9.0.0` | Bootstrap / Model Confidence Set / volatility diagnostics |
| `optuna` | `4.8.0` | Bounded hyperparameter tuning |
| `shap` | `0.51.0` | Feature attribution for tree/nonlinear baselines |
| `sqlglot` | `30.31.0` | AST-level read-only SQL validation |
| `reportlab` | `5.1.0` | Pure-Python ISO 50006 PDF report rendering (`D5=A`) |
| `joblib` | `1.6.0` | Model artifact persistence |
| `pydantic` | `2.14.3` | Configuration and schema validation |
