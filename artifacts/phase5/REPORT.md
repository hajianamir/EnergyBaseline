# Phase 5 Verification & Gate G5 Report — EDA / Mining, Model Families `F1–F10` & Large-Data Performance

- **Branch:** `arena/3611a52f-energybaseline`
- **Base Commit:** `fc0562abddba9c7ab5cfa01ff30dbaf86b17b509` (Phase 4 Gate G4 verified)
- **Date:** `2026-11-12`
- **Scope:** `docs/EXECUTION_PLAN.md` §5 Phase 5, §13 (`F1..F10`, `AC-13`, `AC-30`, `VAL-7`), §16 (`MT-5` cluster-label isolation, clustering validity & bootstrap ARI, LTTB/M4 downsampling, optional descriptive EDA attachment), §18 (`UX-5`, `UX-8`, `QAbstractTableModel` + `fetchMore` + `QSortFilterProxyModel`, removal of 3,000-row table/data ceilings), 1,000,000-row benchmark (`scripts/benchmarks/bench_1m.py`), Gate `G5`

---

## 1. Phase ID, Commit Lineage & Module Inventory

- **Phase ID:** Phase 5 — EDA / Mining, Baseline Model Families (`F1–F10`) & Large-Data Performance (`Gate G5`)
- **Parent Commit SHA:** `fc0562abddba9c7ab5cfa01ff30dbaf86b17b509`
- **Added / Updated Packages, GUI Models, Benchmarks, Workflows & Gate Tests:**
  - `energybaseline/models/` (new package — 12 modules):
    - `base.py` (`ModelEvaluationEntry`, `OptionalBackendSkippedError`, `SampleSizeGateSkippedError`, `prepare_design_frame` enforcing `MT-5`, `compute_baseline_metrics`, `align_oof_predictions` resolving Phase 4 §9 Risk 1, `recommend_autocorr_embargo_gap` resolving Phase 4 §9 Risk 2)
    - `f1_baseline.py` (`F1LinearBaseline`: OLS, WLS, and HAC / Newey-West linear energy baselines with asymptotic $t$-interval coefficient CIs)
    - `f2_ashrae.py` (`F2ChangePointBaseline`: ASHRAE Guideline 14 / ISO 50006 `2P`, `3P-cooling`, `3P-heating`, `4P`, and `5P` piecewise change-point baselines + `compute_variable_base_degree_days` + residual bootstrap knot CIs per `AC-30`)
    - `f3_regularized.py` (`F3RegularizedBaseline`: `Ridge`, `Lasso`, `ElasticNet`, `HuberRegressor`, and `QuantileRegressor` with 3,000-row cap removed + `compute_regularization_path`)
    - `f4_basis.py` (`F4BasisBaseline`: polynomial, cubic B-spline, additive GAM-like smooths with `partial_effect`, and centered pairwise interaction baselines)
    - `f5_ensemble.py` (`F5EnsembleBaseline`: `RandomForest`, `ExtraTrees`, `XGBoost`, `LightGBM`, and `CatBoost` with `allow_writing_files=False` and per-feature monotonic constraints $\{-1, 0, +1\}$)
    - `f6_neural.py` (`F6NeuralBaseline`: deterministic `MLPRegressor` + PyTorch CPU `LSTM` sequence regressor gated by `min_samples` and Lite-profile clean skip)
    - `f7_timeseries.py` (`F7TimeSeriesBaseline`: `SARIMAX` / `ARIMAX` with exogenous regressors and `ETS` / Holt-Winters exponential smoothing)
    - `f8_regime.py` (`F8RegimeBaseline`: operational plant-state and calendar-regime segmented baselines + multi-method rank consensus via Kendall's $W$ concordance `compute_kendalls_w` / `compute_feature_consensus_f8`)
    - `f9_decomposition.py` (`F9DecompositionBaseline`: STL/Fourier + exogenous regressor hybrid with closed-form `formula_text()`, exact additive `decompose_predict()` breakdown, and optional `Prophet` clean skip)
    - `f10_stacking.py` (`F10ConvexStackingBaseline`: chronological out-of-fold simplex-constrained convex stacking $w_m \ge 0, \sum_m w_m = 1$)
    - `__init__.py` (`evaluate_f1_f10_catalog`: strictly gated by `VSG` + `TCP` acceptance per `PIPE-1`, strips exploratory cluster labels per `MT-5`, computes `T10` Model Confidence Set membership, and emits `ModelEvaluationEntry` records)
  - `energybaseline/mining/` (new package — 4 modules):
    - `clustering.py` (`discover_operating_regimes`, Silhouette / Calinski-Harabasz / Davies-Bouldin validity indices, `compute_bootstrap_ari` resampling stability, `DescriptiveClusterLabels`, `attach_descriptive_cluster_labels`, `strip_discovery_cluster_labels`, `assert_no_cluster_labels_in_model_inputs`, `RegimeLabelLeakageError`)
    - `downsample.py` (`lttb_downsample` and `m4_downsample` with quantitative interpolation RMSE and min/max envelope error telemetry)
    - `eda_attach.py` (`build_optional_eda_attachment` & `assert_descriptive_only` stamping every section `"descriptive, not inferential"` and blocking p-values or significance claims)
    - `__init__.py` (public mining exports)
  - `energybaseline/gui/{table_model,widgets,pages_results,pages_setup}.py`:
    - `table_model.py` (`LazyPageSource`, `ParquetPagedSource`, `CallableChunkSource`, `DataFramePagedSource`, `PagedTableModel(QAbstractTableModel)` with `canFetchMore`/`fetchMore` and bounded LRU page cache, `PagedSortFilterProxyModel`)
    - `widgets.py` (`PagedTableView(QTableView)`, `make_table` migrated off `QTableWidget`, `fill_table` with 3,000-row ceiling removed, `make_form_grid` for small interactive schema cell-widget editors)
    - `pages_results.py` (100% `QTableView` / zero `QTableWidget`, `UX-5` `"Model Evaluation Register"` tab + live proxy filter, `UX-8` accessible labels + explicit `setTabOrder` + keyboard shortcuts, M4/LTTB chart downsampling)
    - `pages_setup.py` (300-row preview truncation removed; full paged dataset preview)
  - `energybaseline/core/{eda,mining,models,pipeline,ingest,pool}.py`, `energybaseline/config/runtime_env.py`, `energybaseline/{cli,launcher}.py`, `energybaseline/db/session.py`, `energybaseline/io/paths.py`:
    - Removed 3,000/4,000-row ceilings in `eda.py`, `mining.py`, and `models.py`; wired `MT-5` guard into `evaluate_candidate` and `pipeline.py`; centralized runtime environment helpers under `energybaseline/config/runtime_env.py`; added CLI `clustering-validity` and `model-catalog` E2 subcommands
  - `scripts/benchmarks/bench_1m.py` (1,000,000-row row-grouped Parquet + lazy `fetchMore` + LTTB/M4 + streaming OLS benchmark)
  - `tests/gates/test_phase5_{f1_f10_models,mining_isolation_and_perf}.py` & `tests/gui/test_phase5_gui_tables_ux5_ux8.py` (22 new Phase 5 test executions across 15 parametrized/unit cases + 2 GUI suites; repository total now **201 passing tests**)

---

## 2. Requirement ID -> File / Function -> Test Traceability Table

| ID | File / Function | Verification Test(s) |
|---|---|---|
| `AC-13` / `VAL-7` | `energybaseline/models/__init__.py::evaluate_f1_f10_catalog`; `energybaseline/models/base.py::ModelEvaluationEntry` | `tests/gates/test_phase5_f1_f10_models.py::test_gated_f1_f10_catalog_requires_vsg_tcp_and_builds_complete_register` |
| `AC-30` / `F2` | `energybaseline/models/f2_ashrae.py::F2ChangePointBaseline`, `compute_variable_base_degree_days` | `tests/gates/test_phase5_f1_f10_models.py::test_f2_ashrae_2p_3p_4p_5p_planted_knots_and_hdd_cdd` |
| `F1` | `energybaseline/models/f1_baseline.py::F1LinearBaseline` | `tests/gates/test_phase5_f1_f10_models.py::test_f1_ols_wls_hac_planted_effect_recovery_and_ci` |
| `F3` | `energybaseline/models/f3_regularized.py::F3RegularizedBaseline`, `compute_regularization_path`; `energybaseline/core/models.py` | `tests/gates/test_phase5_f1_f10_models.py::test_f3_regularized_path_outlier_robustness_and_above_3000_rows` |
| `F4` | `energybaseline/models/f4_basis.py::F4BasisBaseline` | `tests/gates/test_phase5_f1_f10_models.py::test_f4_basis_splines_polynomial_and_interactions_planted_recovery` |
| `F5` | `energybaseline/models/f5_ensemble.py::F5EnsembleBaseline` | `tests/gates/test_phase5_f1_f10_models.py::test_f5_ensembles_planted_recovery_and_strict_monotonic_constraints` |
| `F6` | `energybaseline/models/f6_neural.py::F6NeuralBaseline`; `energybaseline/models/base.py::align_oof_predictions`, `recommend_autocorr_embargo_gap` | `tests/gates/test_phase5_f1_f10_models.py::test_f6_neural_mlp_and_pytorch_lstm_planted_recovery_and_sample_size_skip` |
| `F7` | `energybaseline/models/f7_timeseries.py::F7TimeSeriesBaseline` | `tests/gates/test_phase5_f1_f10_models.py::test_f7_sarimax_arimax_and_ets_planted_recovery` |
| `F8` | `energybaseline/models/f8_regime.py::F8RegimeBaseline`, `compute_kendalls_w`, `compute_feature_consensus_f8` | `tests/gates/test_phase5_f1_f10_models.py::test_f8_regime_segmented_recovery_and_kendalls_w_rank_agreement` |
| `F9` | `energybaseline/models/f9_decomposition.py::F9DecompositionBaseline` | `tests/gates/test_phase5_f1_f10_models.py::test_f9_decomposition_hybrid_planted_recovery_and_exact_variable_breakdown` |
| `F10` | `energybaseline/models/f10_stacking.py::F10ConvexStackingBaseline` | `tests/gates/test_phase5_f1_f10_models.py::test_f10_convex_stacking_simplex_weights_and_oof_blend` |
| `MT-5` (Isolation) & Clustering | `energybaseline/mining/clustering.py::discover_operating_regimes`, `compute_bootstrap_ari`, `assert_no_cluster_labels_in_model_inputs` | `tests/gates/test_phase5_mining_isolation_and_perf.py::test_clustering_validity_indices_and_bootstrap_ari_stable_vs_unstable`, `test_mt5_proof_discovery_cluster_labels_never_reach_any_model` |
| LTTB / M4 & Descriptive EDA | `energybaseline/mining/downsample.py::lttb_downsample`, `m4_downsample`; `energybaseline/mining/eda_attach.py::build_optional_eda_attachment` | `tests/gates/test_phase5_mining_isolation_and_perf.py::test_lttb_and_m4_downsampling_bounds_and_chart_sizes`, `test_optional_eda_attachment_strictly_descriptive_not_inferential` |
| `UX-5` / `UX-8` & 1M Tables | `energybaseline/gui/table_model.py::PagedTableModel`, `ParquetPagedSource`; `energybaseline/gui/pages_results.py::ResultsPage`; `scripts/benchmarks/bench_1m.py` | `tests/gui/test_phase5_gui_tables_ux5_ux8.py::test_paged_table_view_fetch_more_on_1m_rows_without_full_materialization`, `test_results_page_zero_qtablewidget_ux5_register_and_ux8_accessibility`, `tests/gates/test_phase5_mining_isolation_and_perf.py::test_1m_row_benchmark_and_phase5_cli_e2_commands` |

---

## 3. Canonical Gate G5 Command Results

All canonical Gate G5 verification commands exited `0`:

1. `pytest -q` -> **`201 passed in 295.42s`** (exit `0`).
2. `pytest -q tests/gates` -> **`169 passed in 156.70s`** (exit `0`).
3. `ruff check . && ruff format --check .` -> **All checks passed; 59 files formatted** (exit `0`).
4. `pyflakes energybaseline` -> **0 warnings / 0 errors** (exit `0`).
5. `python scripts/gates/guard_web_free.py` -> **`OK [guard_web_free] clean`** (exit `0`).
6. `python scripts/gates/guard_env_readers.py` -> **`OK [guard_env_readers] clean`** (exit `0`).
7. `python scripts/gates/guard_tests_integrity.py` -> **`OK [guard_tests_integrity] verified 4 baseline test files`** (exit `0`).
8. `python scripts/gen_matrix.py --check` -> **`OK [gen_matrix] verified 251 requirement IDs (131 done)`** (exit `0`).
9. `pytest -q --cov=energybaseline/core --cov-fail-under=60` -> **`83.21%` on `energybaseline/core` (`86.28%` across `core + tests_catalog + mining + models`; `92.18%` across `mining + models`)** (exit `0`).
10. `pytest -q tests/gui -p no:cacheprovider` -> **`7 passed in 7.78s` (`58.55%` GUI coverage; `88%` on `table_model.py`)** (exit `0`).
11. `test -z "$(grep -rn 'os\.environ' energybaseline/ --exclude-dir=config)"` -> **0 matches** (exit `0`).
12. `test -z "$(grep -rn 'DATABASE_URL' energybaseline/launcher.py)"` -> **0 matches** (exit `0`).
13. `test -z "$(grep -rn 'pickle\.load' energybaseline/)"` -> **0 matches** (exit `0`).
14. `test -z "$(grep -rn 'from flask\|from fastapi\|import uvicorn' energybaseline/)"` -> **0 matches** (exit `0`).
15. `alembic upgrade head && alembic downgrade base && alembic upgrade head` -> **Reversible migration ladder clean** (exit `0`).
16. `python scripts/benchmarks/bench_1m.py` -> **`0.5668 s` total wall time on `1,000,000` rows, `38.93 MiB` peak traced heap, `full_frame_materialized: false`, `peak_resident_rows: 4096`** (exit `0`).

---

## 4. Test Counts, Coverage Summary & 1M-Row Benchmark Telemetry

- **Total Repository Tests:** `201 passed, 0 failed` (`169` gate tests, `7` GUI tests, `25` core/staleness/DB tests).
- **Coverage Summary:**
  - `energybaseline/core + tests_catalog + mining + models` (`12,165` statements): **`86.28%`**
  - `energybaseline/core` (`7,761` statements): **`83.21%`** (exceeds `60%` floor by `+23.21 pp`)
  - `energybaseline/mining + energybaseline/models` (`1,868` statements): **`92.18%`** (`f6_neural.py: 98%`, `f5_ensemble.py: 97%`, `f9_decomposition.py: 97%`, `f10_stacking.py: 96%`, `clustering.py: 95%`, `f2_ashrae.py: 94%`, `f3_regularized.py: 94%`, `f4_basis.py: 93%`, `eda_attach.py: 92%`, `downsample.py: 91%`, `f1_baseline.py: 90%`, `f8_regime.py: 89%`, `f7_timeseries.py: 83%`)
  - `energybaseline/gui` (`3,438` statements): **`58.55%`** (`table_model.py: 88%`, `page_status.py: 86%`, `main_window.py: 84%`)
- **1,000,000-Row Benchmark (`scripts/benchmarks/bench_1m.py`, CI/sandbox non-normative per `D2`):**
  - Streaming 20-row-group Parquet generation (`1,000,000` rows, `34.18 MiB`): **`294.47 ms`**
  - `QAbstractTableModel` + `fetchMore` + `QSortFilterProxyModel` (`21` pages = `10,752` rows paged): **`41.55 ms`**, `peak_resident_rows = 4,096` (`0.41%` of 1M rows), `full_frame_materialized = false`
  - Streaming 1,000,000-row OLS baseline recovery: **`37.36 ms`** (planted `base=120.0, hdd=4.25, cdd=6.10, occ=28.0` -> recovered `120.0026, 4.2497, 6.1000, 27.9988`)
  - LTTB (`1,000,000 -> 2,000` pts): **`145.21 ms`** (`500x` compression); M4 (`1,000,000 -> 3,196` pts at `800 px`): **`29.60 ms`**, `envelope_min_error = 0.0`, `envelope_max_error = 0.0`
  - Peak traced Python heap: **`38.93 MiB`**; total benchmark wall time: **`0.5668 s`**

---

## 5. Artifacts (`E1` & `E2`) + SHA-256 Digests

| Class | Artifact Path | Producer Command / Purpose | SHA-256 |
|---|---|---|---|
| `E1` | `artifacts/phase5/gates_g5.txt` | Ruff, Pyflakes, 4 architecture guards, `gen_matrix.py --check`, 5 hygiene greps, invariant digests | `ba0abb56fac53f11c0e172f3fea1ccd9b60bbe6d3bcd0d4a263d180da06321` |
| `E1` | `artifacts/phase5/alembic_ladder.txt` | Reversible migration `upgrade head -> downgrade base -> upgrade head` | `02b2ce55c836bfcede16d5173b01d3d951cf27e5c8046dc01d9ff1853db7ce24` |
| `E1` | `artifacts/phase5/pytest_gates.txt` | `pytest -q tests/gates` (`169 passed`) | `4d56da1ddd268da0e28b3fde24ba3f781416a57db9a879feeee1a4ac9a754a57` |
| `E1` | `artifacts/phase5/pytest_full.txt` | Full suite (`201 passed`) + `86.28%` combined / `83.21%` `core` coverage | `ae6cefbcbe7289452004fc562a6b987e42458042263556c19c259cac44a9f7df` |
| `E1` | `artifacts/phase5/pytest_gui.txt` | Headless GUI suite (`7 passed`) + `58.55%` GUI coverage | `febdd1cf256aaaa62529bd00f937bba305db0cb731b897886948956b8cefccb3` |
| `E1` | `artifacts/phase5/bench_1m.txt` | `python scripts/benchmarks/bench_1m.py` console log (`0.6385 s`, `16.3 ms` paging) | `af15b7300c5cf1f2911d4aa72f21ba5147b85e3d9ebea67f506bf6a41e01ff80` |
| `E2` | `artifacts/phase5/evidence/e2_bench_1m.json` | Structured 1,000,000-row benchmark output (`full_frame_materialized: false`, `peak_resident_rows: 4096`) | `af15b7300c5cf1f2911d4aa72f21ba5147b85e3d9ebea67f506bf6a41e01ff80` |
| `E2` | `artifacts/phase5/evidence/e2_clustering_and_eda.json` | `python -m energybaseline.cli clustering-validity samples/monthly_building.csv` (Silhouette/CH/DB + bootstrap ARI + `"descriptive, not inferential"` EDA attachment) | `37a99503a7041da7a8b9b45c57cc18a7498ab946801c7e7bbdac21329a7441c8` |
| `E2` | `artifacts/phase5/evidence/e2_model_evaluation_register.json` | `python -m energybaseline.cli model-catalog samples/monthly_building.csv --lite` (VSG/TCP-gated `F1..F10` Model Evaluation Register + `MCS` membership) | `1fdb244be06fceec883270fbbfea70940a0cb35491bd0ff3ad2d9bcc64b635e7` |
| `E2` | `artifacts/phase5/evidence/e2_r_references_check.json` | `python -m energybaseline.cli r-references-check` (`<= 14`-day freshness & R parity verified) | `359abf2f85b5db9442097ce7329ff524e15ca94561d39e848ba2d210c3a5e74b` |
| `E2` | `artifacts/phase5/evidence/e2_system_health.json` | `python -m energybaseline.cli system.health` | `fb05bdbfba5c4dded2cce51af7d4d4a2ebde8d8bd907c85fc766a1509c57ec1b` |
| `E2` | `artifacts/phase5/evidence/e2_system_sbom.json` | `python -m energybaseline.cli system.sbom` (CycloneDX 1.5) | `64a6b33c38eff89a425e2cf4989d6f1a72b288fef40ca7ac8646769b47f3017a` |

---

## 6. `E3` (Windows & Linux 2-OS CI Matrix): Workflow Execution & Reference Hardware Note

- **Automated 2-OS CI Matrix (`.github/workflows/ci.yml`):** Updated job `Phase 5 Gate G5 (${{ matrix.os }})` runs on both `windows-latest` (Python `3.11.9`, native `windows` Qt QPA + packaging/DPI/orphan/Authenticode evidence) and `ubuntu-latest` (Python `3.11.18`, `offscreen` Qt QPA), executing `test_phase5_f1_f10_models.py`, `test_phase5_mining_isolation_and_perf.py`, `scripts/benchmarks/bench_1m.py`, full coverage (`>= 80%` across `core + tests_catalog + mining + models`), and GUI tests (`>= 56%` across `energybaseline/gui`).
- **Decision `D2 = B + A` Disclosure:** GitHub Actions `windows-latest` and Linux container benchmark numbers (`0.5668 s` on `1M` rows) are explicitly labelled **CI, non-normative**; normative physical Windows 11 23H2 8-core / 16 GB / NVMe sign-off remains scheduled for Phases 7 and 9.

---

## 7. Deltas in `deviations.md` / `assumptions.md` / `not_implemented.md`

- **`docs/deviations.md`:** Unchanged (`0` new deviations introduced in Phase 5).
- **`docs/assumptions.md`:** Unchanged.
- **`docs/not_implemented.md`:** Unchanged (`F9-Prophet` optional extra and `F6` PyTorch LSTM skip in Lite profile behave exactly as documented in `docs/not_implemented.md`, raising `OptionalBackendSkippedError` and recording `"SKIPPED"` in the `UX-5` Model Evaluation Register).
- **`docs/requirements.yaml` & `docs/requirements_matrix.md`:** Marked `15` Phase 5 requirement IDs (`AC-13`, `AC-30`, `F1..F10`, `VAL-7`, `UX-5`, `UX-8`) as `status: done`, advancing total verified `done` IDs from `116` to **`131` / `251`**.
- **Phase 4 Carry-Forward Resolution:** Both technical handoff items from `artifacts/phase4/REPORT.md` §9 (`align_oof_predictions` for `F6`/`F7` warm-up row alignment and `recommend_autocorr_embargo_gap` for persistence-scaled CV embargo sizing) are implemented and unit-tested in `energybaseline/models/base.py`.

---

## 8. What Was Not Proven and Why (Carried Forward + Phase 5 Scope Boundaries)

1. **Full Nested Walk-Forward Validation (`VAL-1..VAL-6`, `AC-14`), Composite Scoring (`SC-1..SC-10`), Conformal UQ (`UQ-1..5`), and SHAP Attribution (`AC-31`):** Explicitly scoped to **Phase 6 (`Gate G6`)** per the Phase 5 execution boundary. Phase 5 establishes all 10 `F1..F10` model families, OOF alignment/embargo primitives, and the `VAL-7` / `UX-5` Model Evaluation Register; Phase 6 wires full nested walk-forward CV, leakage canaries, MAPIE conformal calibration, and transparent composite scoring across the unified pipeline.
2. **DPAPI Credential Hardening & External SQL TLS (`SCR-1..6`, `AC-03`) and Post-Baseline CUSUM/EWMA Drift Monitoring (`PM-1..7`, `DR-1..5`, `MT-1..5`):** Scheduled for **Phase 7 (`Gate G7`)**.
3. **Deterministic ReportLab PDF & `.ebproj` Signed Bundle Packaging (`RPT-1..7`, `AC-25`, `AC-26`) and Physical Windows 11 23H2 Reference Benchmarks (`D2`, `PER-1..6`):** Scheduled for **Phases 7, 8, and 9** (carried forward from Phase 4 §8).

---

## 9. Open Risks and Their Effect on Phase 6 (Validation `VAL-1..6`, Scoring `SC-1..10` & UQ `UQ-1..5`)

1. **Nested Optuna / Grid Budget Inside Outer Walk-Forward Folds (`VAL-2` / `VAL-3` -> Phase 6):** Evaluating `F5` gradient-boosted ensembles (`XGBoost`, `LightGBM`, `CatBoost`), `F6` (`LSTM`), and `F7` (`SARIMAX`) inside nested walk-forward folds can multiply wall-clock fit time if inner trials are unconstrained; Phase 6 must enforce strict per-family trial/time caps and sample-size gates inside inner folds.
2. **Conformal Interval Calibration Under Regime Shifts & Serial Correlation (`UQ-1` / `F8` -> Phase 6):** Standard exchangeable split conformal intervals under-cover when residuals are serially correlated or heteroskedastic across plant regimes; Phase 6 must pair `F1..F10` point predictions with sequential EnbPI / ACI conformal calibration (`UQ-1`) and verify empirical 80%/95% holdout coverage (`UQ-3`).
3. **14-Day R Reference Rolling Freshness (`TC-8` / `CI-3`):** Carried forward from Phase 4 §9 — committed R reference JSONs (`9` days old as of `2026-11-12`, within the `<= 14`-day window) must continue to be refreshed by `.github/workflows/r-reference.yml` before `2026-11-17`.
