# Phase 6 Gate G6 Verification & Evidence Report

**Branch:** `arena/3611a52f-energybaseline`  
**Phase:** Phase 6 — Modelling, Validation, 13-Criteria Scoring, Pre-Lock Top-5 & Immutable Baseline Lock (`Gate G6`)  
**Language & Encoding:** 100% English (`G-7`), UTF-8

---

## 1. Scope Delivered (`docs/EXECUTION_PLAN.md` §17, §18)

### 1.1 Leakage-Free Temporal Validation & Purge Architecture (`VAL-1..VAL-6`, `AC-14`)
- **`energybaseline/validation/splitting.py` (`VAL-1`):** Strict chronological train / validation / holdout splitting (`chronological_train_val_holdout_split`); raises `TemporalLeakageError` if `shuffle=True` or timestamps are non-monotonic.
- **`energybaseline/validation/purge.py` & `walkforward.py` (`VAL-2`):** Expanding and rolling walk-forward cross-validation (`WalkForwardMode.EXPANDING`, `WalkForwardMode.ROLLING`) with `PurgeGapSpec` computing `purge_steps = max(1 day, 1 step, max_lag_steps, max_rolling_window_steps)`. Verified strictly monotonically growing with lag/rolling horizon (never stuck at 1 day).
- **`energybaseline/validation/nested_hpo.py` (`VAL-3`):** Optuna TPE nested hyperparameter tuning strictly inside inner walk-forward folds with bounded `n_trials` and `timeout_seconds`.
- **`energybaseline/validation/preprocessing.py` & `energybaseline/pipeline.py` (`VAL-4`):** Pipeline-encapsulated `InFoldPreprocessor` and `InFoldModelPipeline` fitting imputation medians, `StandardScaler`, degree-day balance knots (`1P..5P` grid), causal lags, and mutual-information / correlation top-$k$ feature selection (`k_max = min(p, floor(n / 20))`) strictly inside each training fold.
- **`energybaseline/validation/leakage_canary.py` (`VAL-5`):** Automated future-target perturbation canary (`run_future_perturbation_canary`) asserting exact `0.0` historical fold prediction sensitivity under $+1000\sigma$ future target shocks, while catching deliberately leaky global pipelines.
- **`energybaseline/validation/ashrae14_metrics.py` (`VAL-6`, `DOM-2`):** Out-of-sample `CV(RMSE)` (standard + ASHRAE 14 DOF-adjusted $\sqrt{\text{SSE}/(n-p)}/\bar{y}$), `NMBE`, $R^2$, `MAE`, `MAPE`, `sMAPE`, and `WAPE` with granularity-aware compliance checks (`monthly`, `daily`, `hourly`).

### 1.2 Mandatory `[M]` Model Portfolio, NN Gate & Inner-Fold NNLS Ensembles (`energybaseline/models/`)
- **`portfolio.py`:** Full 16-wrapper `[M]` baseline portfolio (`mean`, `degree_day`, `mlr`, `ridge`, `lasso`, `elasticnet`, `piecewise_1p_5p`, `towt`, `random_forest`, `extra_trees`, `gradient_boosting`, `xgboost`, `lightgbm`, `svr_rbf`, `mlp`, `lstm`) plus `[S]`/`[O]` tier tracking in `get_portfolio_registry()`. Every `[M]` wrapper verified for planted physical signal recovery (`tests/gates/test_phase6_validation_and_portfolio.py`).
- **`base.py`:** `evaluate_nn_gate` tested in both directions (accepted when sample size, linearity gap, and hardware budget pass; rejected/skipped with explicit logged reason otherwise) + `assert_no_cluster_labels_in_model_inputs` (`RegimeLabelLeakageError`, `MT-5`) called before any DataFrame coercion.
- **`ensemble.py`:** `InnerFoldNNLSEnsemble` fitting non-negative simplex weights (`scipy.optimize.nnls`) **exclusively on inner-fold OOF predictions**, enforcing `k_max = min(K, floor(n_inner / 20))` active base learners, and accepting the ensemble on outer folds **only when relative RMSE gain $> 3.0\%$** (`min_relative_gain_pct=3.0`).

### 1.3 13-Criteria Composite Scoring, Non-Commingled Registers, Envelope & Override (`SC-1..SC-9`, `AC-15`, `AC-17`, `AC-18`)
- **`scoring/composite.py` (`SC-1`, `SC-3`, `SC-5`, `SC-8`, `AC-15`, `AC-17`, `AC-18`):** 13-criteria transparent weighted composite score (`r2`, `cvrmse`, `nmbe`, `mae_smape`, `fold_stability`, `parsimony`, `residual_whiteness`, `homoskedasticity`, `influence_robustness`, `physical_plausibility`, `mcs_membership`, `structural_stability`, `computational_cost`), $\pm 20\%$ coordinate-wise weight perturbation sensitivity analysis, deterministic tie-break key, and pairwise Diebold–Mariano (HLN `T10`) + Hansen MCS + Friedman/Nemenyi (`T11`) matrix.
- **`scoring/tiebreak.py` (`SC-4`):** Parsimony and physical-interpretability tie-breaking within the Model Confidence Set (`T10`) and Cook's-$D$ (`T13`) stability tolerance.
- **`scoring/registers.py` (`SC-2`, `SC-9`):** Strictly disjoint `shortlisted_top5`, `qualified_not_shortlisted`, `rejected`, and `skipped` registers (`verify_non_commingled()`), plus `SC-9` zero-qualified explicit engineer acknowledgement workflow.
- **`scoring/envelope.py` (`SC-9`):** Multi-driver baseline validity domain envelope (min/max, $P_{01}/P_{99}$, robust Mahalanobis / 2D convex-hull check) emitting extrapolation warnings on reporting-period driver excursions.
- **`scoring/override.py` (`SC-7`):** Manual engineer baseline override requiring a non-empty justification ($\ge 15$ chars) recorded in an immutable SHA-256 `ManualOverrideRecord`.

### 1.4 Pre-Lock Top-5 Shortlist & Immutable Versioned Baseline Lock (`SC-6`, `SC-10`, `AC-04`, `AC-31`, `G-9`, `DOM-1`)
- **`models/top5.py` & `gui/pages_results.py` (`SC-6`, `AC-31`):** `Top5ShortlistView` (`visible_before_lock=True`) displaying closed-form equations + HAC standard errors for parametric baselines, and architecture + hyperparameters + explicitly bannered **`APPROXIMATE_DIAGNOSTIC_SURROGATE`** (`is_surrogate=True`, `fidelity_r2`) + Shapley attributions for non-parametric baselines (never presented as the model's actual formula). Separate `PagedTableView` widgets (`qualified_other_tbl` vs `register`) in `ResultsPage`.
- **`lock/baseline_lock.py` & `core/store.py` (`SC-10`, `AC-04`, `G-9`, `DOM-1`):** Pickle-free `joblib` serialization with pre-load SHA-256 verification (`ArtifactIntegrityError` on 1-byte tamper), immutable `LockedBaselineRecord` (`MappingProxyType` + `__setattr__` guard raising `ImmutableBaselineLockError`), hash-chained `BaselineLockManager` version ledger (`v1 -> v2`), ISO 50006 model card, and live `openpyxl` Excel formula round-trip verification (`=Coefficients!...`).

---

## 2. Requirements Traceability Updated (`docs/requirements.yaml` -> `docs/requirements_matrix.md`)

25 Phase 6 requirement IDs transitioned to `done` with verified `code_refs` and `test_ids` (`156 / 251` total `done`; Phase 7–9 items `UQ-1..5`, `AC-16`, `AC-32`, `MT-1..5`, `DR-1..5`, `PM-1..7` remain `planned`):
`AC-04`, `AC-14`, `AC-15`, `AC-17`, `AC-18`, `AC-31`, `G-9`, `VAL-1`, `VAL-2`, `VAL-3`, `VAL-4`, `VAL-5`, `VAL-6`, `SC-1`, `SC-2`, `SC-3`, `SC-4`, `SC-5`, `SC-6`, `SC-7`, `SC-8`, `SC-9`, `SC-10`, `DOM-1`, `DOM-2`.

---

## 3. Verification & Coverage Summary

| Check / Suite | Result | Details |
|---|---|---|
| `ruff check .` & `ruff format --check .` | **PASS (`0`)** | Zero lint or formatting violations across repository |
| `pyflakes energybaseline` | **PASS (`0`)** | Zero undefined names or unused imports |
| Guard scripts (`web_free`, `env_readers`, `tests_integrity`, `no_qthread_compute`) | **PASS (`0`)** | All 4 guards clean; `tests/.baseline.sha256` intact |
| Canonical `grep` assertions (6 checks) | **PASS (`0`)** | Zero `pickle.load`, unauthorized `os.environ`, `DATABASE_URL` in launcher, web frameworks, non-GUI `post_hoc`, or `QTableWidget` in `pages_results.py` |
| `scripts/gen_matrix.py --check` | **PASS (`0`)** | `251` IDs verified (`156 done`) |
| Alembic reversible migration cycle | **PASS (`0`)** | `upgrade head -> downgrade base -> upgrade head` |
| R reference freshness & numerical parity | **PASS (`0`)** | `age_days = 1.91 < 14`, `parity.ok = true` |
| Full Core + Catalog + Mining + Models + Scoring + Validation + Lock suite | **PASS (`0`)** | **216 passed**, combined coverage **`86.89%`** (`>= 80%` & `>= 60%` gates) |
| Headless GUI suite (`tests/gui -p no:cacheprovider`) | **PASS (`0`)** | **8 passed**, GUI coverage **`64.33%`** (`>= 56%` gate; `pages_results.py` at `83%`) |
| `pip-audit -r requirements.lock` | **PASS (`0`)** | No known vulnerabilities found |

---

## 4. Evidence Manifest (`artifacts/phase6/`)

- **`E1` Command Logs:**
  - `artifacts/phase6/e1_static_guards_alembic_bench.txt` — Static linters, AST guards, traceability matrix check, 6 grep assertions, Alembic cycle, 1M-row streaming benchmark, and R reference freshness/parity check.
  - `artifacts/phase6/e1_pytest_g6_and_gui.txt` — Gate G6 validation/portfolio/scoring/Top-5/lock test suite, full GUI test suite with coverage report, and `pip-audit`.
- **`E2` Product-Generated JSON Artifacts:**
  - `artifacts/phase6/system_health.json` — Runtime `system.health` self-check (`ok: true`).
  - `artifacts/phase6/sbom.json` — Reproducible CycloneDX 1.5 runtime SBOM.
  - `artifacts/phase6/model_evaluation_register.json` — Product CLI `model-catalog` F1–F10 evaluation register on `samples/monthly_building.csv`.
  - `artifacts/phase6/scoring_register.json` — Product CLI `scoring-register` 13-criteria ranking, $\pm 20\%$ weight sensitivity, separated `shortlisted_top5` / `qualified_not_shortlisted` / `rejected` / `skipped` registers, validity envelope, and pre-lock Top-5 view (`visible_before_lock: true`).
  - `artifacts/phase6/locked_baseline.json` — Product CLI `baseline-lock` immutable `LockedBaselineRecord` (`lock-4a691b354f3f`, SHA-256 verified) + Excel formula round-trip (`=Coefficients!$B$2+(C2*Coefficients!$B$3)`, `max_abs_prediction_diff = 3.638e-12`).
  - `artifacts/phase6/bench_1m.json` — 1M-row Parquet streaming OLS + LTTB/M4 + LRU table paging benchmark (`ok: true`, `peak_traced_mib: 38.8`).
- **`E3` Multi-OS GitHub Actions CI:**
  - Triggered on push of Phase 6 commit to `arena/3611a52f-energybaseline` (`windows-latest` + `ubuntu-latest`).
