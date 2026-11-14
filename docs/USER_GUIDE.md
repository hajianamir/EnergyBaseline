# EnergyBaseline v3 - User Guide

EnergyBaseline is an **offline, single-user Windows desktop application** (Python + PySide6). There is no browser, no web server and no
localhost URL. Double-click **`EnergyBaseline.exe`** (or the `EnergyBaseline.lnk` shortcut) to start.

## 1. Workflow in six tabs

| Tab | What you do |
|---|---|
| **1 Data & Temporal Setup** | Open CSV / Excel / Access (or *Manual entry*), choose the timestamp column, tick one or more **TARGET** columns, declare the sampling period, aggregation, timezone/DST and the baseline / reporting windows. *Validate temporal setup* shows declared vs. detected sampling. |
| **2 Analysis** | *Run analysis* (F5). Each target runs independently with its own progress bar; *Cancel* stops after the current step. |
| **3 Results** | Leaderboard, decision rationale, comparative diagnostics, rejected-models register, exclusions, EDA, data mining, interpretability, **Report** (PDF / Excel / HTML). |
| **4 Baseline Selection (Top-5)** | For every target the five best qualified models are shown with their **explicit formula** (or exact architecture + exported artifact), metrics, uncertainty, rationale. Pick one (radio button), add an optional note and press **Lock baseline**. |
| **5 Prediction & Monitoring** | Unlocked per target after locking. Feed new data by **file**, **external SQL** or **manual entry**; get baseline, deviation, cumulative deviation, alerts and a dashboard. |
| **Status** | Database self-test `{ok, url, fallback, writable}`, storage paths, versions, audit log. |

## 2. AUTO everywhere
Open **Advanced -> Settings**. Every option has an **AUTO** checkbox (ticked by default) plus its recommended value and an explanation.
With AUTO, the application decides from the data (e.g. *missing rate 0.4 % < 5 % -> linear interpolation*) **per target**, and logs the setting,
chosen value, the signals and the rule. The read-only **AUTO Resolutions** dock (and report section 18) shows all decisions after a run.
AUTO is deterministic: same data + seed -> same decisions. Any override is logged as `USER_OVERRIDE`.

## 3. Temporal rules
* Data finer than declared -> it must be aggregated (mean/sum/max/first/last; AUTO = sum for energy-like columns, mean otherwise).
* Data coarser than declared -> **refused** (the application never disaggregates). Declare the real period instead.
* Duplicates, gaps and irregular sampling are flagged; gaps remain missing (never fabricated).

## 4. Prediction & Monitoring
1. **File**: Excel/CSV. Missing columns, non-numeric values and out-of-training-range values are reported *before* prediction.
2. **External SQL** (optional, needs network access to *your* database): *Connections…* -> MySQL, SQL Server, PostgreSQL, SQLite or generic ODBC. Passwords and query templates are encrypted (Windows DPAPI). Only read-only `SELECT` queries are accepted. If the server is unreachable you get an offline notice; nothing crashes.
3. **Manual**: auto-generated form with range warnings, plus a batch table (paste from Excel with Ctrl+V).

Computed per timestamp: baseline, 95 % interval, actual, deviation, deviation %, cumulative deviation, normalised deviation
(deviation / out-of-fold RMSE) and alert flags. Alert threshold AUTO rule: `max(10 %, 2 x CV(RMSE))`; or choose *percent*, *k x RMSE* or *outside the 95 % interval*.
The dashboard can show all locked targets side by side, filter by date, list the top-N deviating records, export PDF/Excel and save a **snapshot** into SQLite.

## 5. Where is my data stored?
Everything stays on your computer, next to the application:

| Location | Content |
|---|---|
| `data\app.db` | SQLite database: datasets, runs, AUTO resolutions, model results, selections (with history), audit log, snapshots, encrypted connections |
| `data\uploads\` | Byte-identical copies of every imported file (raw-data retention) |
| `data\models\run_<n>\<target>\` | Exported model artifacts (`.joblib`) for the Top-5 models |
| `data\runs\run_<n>\results.joblib` | Full analysis results (reloadable via *File -> Load previous analysis run*) |
| `data\reports\run_<n>\` | Generated PDF / Excel / HTML reports |
| `logs\energybaseline.log` | Application log |

If the environment variable `DATABASE_URL` (or `POSTGRES_URL`, `DB_CONNECTION_STRING`, `DB_URI`) is empty or unset, the application uses
`sqlite:///./data/app.db`. `EnergyBaseline.exe` always exports that value on start. If a configured external database is unreachable the app
falls back to SQLite and logs the event.

## 6. Interpreting the model selection
Acceptance uses ASHRAE Guideline 14 / IPMVP guidance (hourly & daily: CV(RMSE) <= 30 %, |NMBE| <= 10 %; monthly: 15 % / 5 %; weekly interpolated;
R² >= 0.75 monthly, 0.5 otherwise). Scores combine 13 weighted criteria (accuracy, bias, robustness across folds, parsimony, residual
diagnostics, physical plausibility, interpretability tier, ASHRAE alignment, uncertainty, data-sufficiency penalty for deep models).
Neural networks are rejected explicitly when data are insufficient or when they do not beat the best classical model by the configured margin.

## 7. Troubleshooting
* *Access import fails*: install the 64-bit Microsoft Access Database Engine, or export the table to CSV.
* *PDF engine*: interim builds use the built-in Qt (or matplotlib) PDF fallback; the ratified target PDF renderer is pure-Python ReportLab (Phase 8).
* *Rebuilding the exe*: run `install.bat` on Windows 10/11 (creates `EnergyBaseline.exe`, the icon and the `.lnk`).

## Phase 7: calibrated intervals and verified monitoring

The strict monitoring API is `energybaseline.monitoring.predict_reporting`; the
legacy dashboard remains available and now saves version-bound snapshots. Drift
recommendations never alter the selected model. Negative residuals within the
training domain can indicate **possible efficiency improvement**, not causal proof.

### Uncertainty calibration

```bash
python -m energybaseline.cli uq-calibrate baseline.csv --targets Energy_kWh --drivers Temperature_C,Production --time-col timestamp --method aci --out uq.json
```

Use chronological, complete numeric observations. The command trains on the first
50%, reserves 20% for calibration (split methods), and diagnoses the last 30%.
EnbPI/ACI predict each reporting outcome **before** observing it. ACI updates its
confidence level and conformity scores, not model parameters. `split`, `cross` and
`jackknife` are exchangeable-data diagnostics, not time-series coverage guarantees.
Exit 1 means empirical coverage failed the ±5 percentage-point tolerance; inspect
the JSON rather than treating interval output as calibrated. Kupiec and
Christoffersen diagnostics can flag marginal or clustered coverage failures.

### Locked-estimator monitoring CLI

```bash
python -m energybaseline.cli monitoring-run reporting.csv --baseline locks/baseline.lock.json --config monitoring_schema.json --reference calibration.csv --history monitoring.jsonl --batch-id reporting-2025-01 --confidence 0.95 --out snapshot.json
```

This CLI accepts a Phase 6 **estimator** lock with a full validity envelope; legacy
desktop bundle locks continue through the desktop workflow. The config contains:

```json
{"drivers": ["Temperature_C", "Production"], "units": {"Temperature_C": "C", "Production": "t", "Energy_kWh": "kWh"}, "step_seconds": 86400, "time_column": "timestamp"}
```

Both files must contain the timestamp, listed drivers and target, with matching
units and exact baseline granularity. Missing values, nonnumeric values, duplicate
or unordered timestamps, additional columns and silent resampling are rejected.
Calibration data must precede reporting data and remain fixed across appends.
Non-routine adjustments in the Python API require an event ID, engineering reason
and approver; amounts are energy per reporting row, with static-factor uncertainty
fully correlated across rows. Routine weather/production adjustments are model
predictions at reporting drivers and are separately recorded.

Each batch records the verified baseline version/hash and original input hash.
The append-only history continues cumulative savings, confidence bands, CUSUM and
EWMA across batches. Duplicate or overlapping periods and changed calibration or
adjustment policies are rejected. Start a separate history for a changed policy
or baseline; simultaneous writers are unsupported. Near-zero EnPI denominators
and fractional savings uncertainty are explicitly undefined rather than infinite.

Causal M&V (`energybaseline.causal`) requires an accepted VSG/TCP before inference.
It exposes balance, propensity overlap, pre-trends, kernel-balancing diagnostics,
DID/HAC intervals and placebo refutation. EconML is installed; DoWhy is optional
and incompatible with the current locked SciPy version. Results are labelled
**possible savings / non-routine event** and disclose identification assumptions.

Reproduce product evidence without mocks:

```bash
python scripts/evidence/phase7.py --out-dir artifacts/phase7/reproduced
```

D2 physical hardware and human visual acceptance remain separate from CI evidence.
