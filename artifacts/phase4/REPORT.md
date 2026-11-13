# Phase 4 Verification & Gate G4 Report — Statistical Test Catalog (`T1–T14`)

- **Branch:** `arena/3611a52f-energybaseline`
- **Base Commit:** `7c957f074b93ce7906da979fa21ddb409378542c` (Phase 3 Gate G3 verified)
- **Date:** `2026-11-12`
- **Scope:** `docs/EXECUTION_PLAN.md` §5 Phase 4, §14 (`T1..T14`, `TC-1..8`), §15 (`CI-1..4`), §27 (R references & 14-day staleness gate), `G-5`, `AC-10`, `AC-11`, `AC-12`, `CI-3`, Gate `G4` (`G4a`, `G4b`, `G4c`)

---

## 1. Phase ID, Commit Lineage & `git diff --stat`

- **Phase ID:** Phase 4 — Statistical Test Catalog (`T1–T14`) & R Reference Parity (`Gate G4`)
- **Parent Commit SHA:** `7c957f074b93ce7906da979fa21ddb409378542c`
- **Added / Updated Modules, R References, Workflows & Gate Tests:**
  - `energybaseline/tests_catalog/` (new package — 18 modules):
    - `tc_assumptions.py` (`CatalogTestResult`, `AssumptionCheck`, `ObservedPowerForbiddenError`, `RawTestArtifact` bridge, `apply_family_multiplicity`, `evaluate_policy_decision`, `"not run (user)"` / `"not run (no group column)"`, Fast-mode subsampling metadata, library version snapshot)
    - `ci_method.py` (HAC / Newey-West SE & mean CI, stationary / moving-block bootstrap CI, Fisher-z correlation CI, delta-method CI, `"naive"` labelling guard on autocorrelated series without HAC / block bootstrap)
    - `t1_stationarity.py` (ADF + KPSS + 4-cell 2x2 stationarity decision matrix)
    - `t2_seasonality.py` (ACF/PACF seasonal peak significance, Canova-Hansen seasonal stability test, Kruskal-Wallis + QS non-parametric fallback)
    - `t3_autocorrelation.py` (Ljung-Box multi-lag Q test + Durbin-Watson residual serial correlation)
    - `t4_normality.py` (Shapiro-Wilk `n <= 5000`, D'Agostino-Pearson $K^2$, Anderson-Darling, Jarque-Bera)
    - `t5_heteroskedasticity.py` (Breusch-Pagan, White, Koenker studentized LM fallback under excess kurtosis, ARCH-LM)
    - `t6_multicollinearity.py` (VIF + Belsley-Kuh-Welsch singular-value condition index & variance-decomposition proportions + Spearman rank-VIF fallback)
    - `t7_structural_break_chow.py` (Chow known-date structural break F-test + R reference parity)
    - `t8_structural_break_quandt_andrews.py` (Quandt-Andrews unknown-date `sup-F` with parametric bootstrap p-value + R reference parity)
    - `t9_pettitt.py` (Pettitt non-parametric rank change-point test + R reference parity)
    - `t10_diebold_mariano.py` (Harvey-Leybourne-Newbold Diebold-Mariano forecast comparison + Hansen-Lunde-Nason Model Confidence Set on OOF + R reference parity)
    - `t11_nemenyi.py` (Friedman omnibus + Nemenyi critical-difference post-hoc comparison on OOF + R reference parity)
    - `t12_little_mcar.py` (Native multivariate Gaussian EM Little's MCAR $\chi^2$ test + R reference parity)
    - `t13_outlier_influence.py` (Strictly flag-only Cook's distance $D_i > 4/n$, Bonferroni externally Studentized residuals, Generalized ESD, plus Ramsey RESET / Harvey-Collier & lagged CCF / bivariate Granger helpers — zero silent row deletion)
    - `t14_causal_group.py` (Causal M&V with `group` column: SMD covariate balance, Welch / Mann-Whitney / Brunner-Munzel location check, pre-intervention parallel trends, logistic propensity overlap, plus multi-group One-Way / Welch ANOVA / Kruskal-Wallis — runs ONLY when a `group` column is present)
    - `monte_carlo.py` (Monte Carlo Type I error calibration under $H_0$ and planted-effect power $\ge 0.80$ across all `T1..T14`, plus prospective design power; retrospective `"observed power"` strictly prohibited)
    - `r_reference_validator.py` & `__init__.py` (7-fixture parity validator, 14-day staleness gate, `run_full_test_catalog` enforcing `VSG` + `TCP` acceptance and `pre_model` / `post_model` / `causal_group` stage placement)
  - `tests/reference/{chow,quandt_andrews,pettitt,diebold_mariano,nemenyi,little_mcar,mcs}.R` & synchronized `.json` fixture bundles in `tests/reference/`, `energybaseline/tests_catalog/r_references/`, and `tests_catalog/r_references/`
  - `.github/workflows/r-reference.yml` (`CI-3` nightly R `4.4.1` live execution + parity validation + automated GitHub issue creation on failure) & `.github/workflows/ci.yml` (independent sub-gates `G4a`, `G4b`, `G4c` + `tests_catalog` coverage gate)
  - `energybaseline/core/{cache,pipeline,report,report_template,temporal}.py`, `energybaseline/cli.py`, & `energybaseline/gui/pages_results.py` (`CachedComplianceRunner.execute_catalog_suite` & 0-engine-call `what_if_catalog`, `TargetResult.test_catalog`, HTML Section 15 + Excel `TestCatalog_*` export, CLI `test-catalog` / `r-references-check`, GUI `"Test Catalog (T1-T14)"` tab, mixed-offset ISO timestamp parsing fix in `temporal.py`)
  - `tests/test_reference_staleness.py` & `tests/gates/test_phase4_{g4a_t1_t4,g4b_t5_t9,g4c_t10_t14}.py` (38 new Phase 4 gate & staleness test executions)

---

## 2. Requirement ID -> File / Function -> Test Traceability Table

| ID | File / Function | Verification Test(s) |
|---|---|---|
| `AC-10` / `TC-1` | `energybaseline/tests_catalog/tc_assumptions.py::CatalogTestResult`; `energybaseline/tests_catalog/__init__.py::run_full_test_catalog` | `tests/gates/test_phase4_g4a_t1_t4.py::test_t1_stationarity_pass_and_fail_plus_2x2_matrix`, `test_g4a_monte_carlo_type1_and_power`, `test_g4b_monte_carlo_type1_and_power`, `test_g4c_monte_carlo_type1_and_power` |
| `AC-11` / `TC-4` | `energybaseline/tests_catalog/tc_assumptions.py::apply_family_multiplicity`, `evaluate_policy_decision`; `energybaseline/core/cache.py::CachedComplianceRunner.what_if_catalog` | `tests/gates/test_phase4_g4c_t10_t14.py::test_stage_placement_gate_enforcement_zero_call_whatif_and_not_run_user_in_report` |
| `AC-12` / `TC-7` / `TC-8` | `energybaseline/tests_catalog/r_reference_validator.py::validate_python_against_r_references`, `check_r_reference_freshness`; `energybaseline/tests_catalog/tc_assumptions.py::ObservedPowerForbiddenError` | `tests/test_reference_staleness.py::test_python_implementations_match_all_seven_r_reference_fixtures`, `test_stale_r_reference_older_than_14_days_fails_gate`, `tests/gates/test_phase4_g4a_t1_t4.py::test_observed_power_strictly_prohibited_and_fast_subsampling_documented` |
| `G-5` / `TC-2` / `TC-3` / `TC-5` / `TC-6` | `energybaseline/tests_catalog/tc_assumptions.py`; `energybaseline/tests_catalog/ci_method.py::resolve_autocorrelation_inference`, `mean_ci_with_autocorrelation_guard`; `energybaseline/core/report.py` | `tests/gates/test_phase4_g4a_t1_t4.py::test_naive_labelling_on_autocorrelated_series_without_hac_and_ci_levels`, `tests/gates/test_phase4_g4b_t5_t9.py::test_g4b_naive_labelling_when_autocorrelated_without_hac` |
| `T1` | `energybaseline/tests_catalog/t1_stationarity.py::run_t1_stationarity`, `stationarity_decision_matrix` | `tests/gates/test_phase4_g4a_t1_t4.py::test_t1_stationarity_pass_and_fail_plus_2x2_matrix` |
| `T2` | `energybaseline/tests_catalog/t2_seasonality.py::run_t2_seasonality` | `tests/gates/test_phase4_g4a_t1_t4.py::test_t2_seasonality_pass_and_fail_plus_kruskal_fallback` |
| `T3` | `energybaseline/tests_catalog/t3_autocorrelation.py::run_t3_autocorrelation` | `tests/gates/test_phase4_g4a_t1_t4.py::test_t3_autocorrelation_pass_and_fail` |
| `T4` | `energybaseline/tests_catalog/t4_normality.py::run_t4_normality` | `tests/gates/test_phase4_g4a_t1_t4.py::test_t4_normality_pass_and_fail` |
| `T5` | `energybaseline/tests_catalog/t5_heteroskedasticity.py::run_t5_heteroskedasticity` | `tests/gates/test_phase4_g4b_t5_t9.py::test_t5_heteroskedasticity_pass_and_fail_plus_kurtosis_fallback` |
| `T6` | `energybaseline/tests_catalog/t6_multicollinearity.py::run_t6_multicollinearity` | `tests/gates/test_phase4_g4b_t5_t9.py::test_t6_multicollinearity_pass_and_fail_plus_rank_vif_fallback` |
| `T7` | `energybaseline/tests_catalog/t7_structural_break_chow.py::run_t7_chow`, `compute_chow_exact`; `tests/reference/chow.R` | `tests/gates/test_phase4_g4b_t5_t9.py::test_t7_chow_pass_and_fail_and_r_reference_parity` |
| `T8` | `energybaseline/tests_catalog/t8_structural_break_quandt_andrews.py::run_t8_quandt_andrews`, `compute_quandt_andrews_profile`; `tests/reference/quandt_andrews.R` | `tests/gates/test_phase4_g4b_t5_t9.py::test_t8_quandt_andrews_pass_and_fail_bootstrap_p_and_r_reference` |
| `T9` | `energybaseline/tests_catalog/t9_pettitt.py::run_t9_pettitt`, `compute_pettitt_exact`; `tests/reference/pettitt.R` | `tests/gates/test_phase4_g4b_t5_t9.py::test_t9_pettitt_pass_and_fail_and_r_reference` |
| `T10` | `energybaseline/tests_catalog/t10_diebold_mariano.py::run_t10_diebold_mariano`, `compute_diebold_mariano_hln_exact`, `compute_mcs_exact`; `tests/reference/{diebold_mariano,mcs}.R` | `tests/gates/test_phase4_g4c_t10_t14.py::test_t10_diebold_mariano_and_mcs_pass_fail_and_r_references` |
| `T11` | `energybaseline/tests_catalog/t11_nemenyi.py::run_t11_nemenyi`, `compute_friedman_nemenyi_exact`; `tests/reference/nemenyi.R` | `tests/gates/test_phase4_g4c_t10_t14.py::test_t11_nemenyi_pass_fail_and_r_reference` |
| `T12` | `energybaseline/tests_catalog/t12_little_mcar.py::run_t12_little_mcar`, `compute_little_mcar_em`; `tests/reference/little_mcar.R` | `tests/gates/test_phase4_g4c_t10_t14.py::test_t12_little_mcar_pass_fail_and_r_reference` |
| `T13` | `energybaseline/tests_catalog/t13_outlier_influence.py::run_t13_outlier_influence`, `compute_ramsey_reset_harvey_collier`, `compute_lagged_ccf_and_granger` | `tests/gates/test_phase4_g4c_t10_t14.py::test_t13_outlier_influence_strictly_flag_only_zero_mutation`, `test_auxiliary_ramsey_reset_granger_and_multigroup_anova_kruskal` |
| `T14` | `energybaseline/tests_catalog/t14_causal_group.py::run_t14_causal_group`, `compute_multi_group_anova_kruskal` | `tests/gates/test_phase4_g4c_t10_t14.py::test_t14_causal_group_only_runs_with_group_column_and_pass_fail` |
| `CI-3` | `.github/workflows/r-reference.yml`; `tests/test_reference_staleness.py` | `tests/test_reference_staleness.py::test_all_seven_r_reference_bundles_exist_and_match_byte_for_byte`, `test_stale_r_reference_older_than_14_days_fails_gate` |

---

## 3. Commands Executed & Raw Output Paths (`E1`)

| Command | Exit Code | Raw Output Artifact |
|---|---|---|
| `python -m ruff check . && python -m ruff format --check .` | `0` | `artifacts/phase4/gates_g4.txt` |
| `python -m pyflakes energybaseline` | `0` | `artifacts/phase4/gates_g4.txt` |
| `python scripts/gates/guard_web_free.py` | `0` | `artifacts/phase4/gates_g4.txt` |
| `python scripts/gates/guard_env_readers.py` | `0` | `artifacts/phase4/gates_g4.txt` |
| `python scripts/gates/guard_tests_integrity.py` (`4` baseline test files intact) | `0` | `artifacts/phase4/gates_g4.txt` |
| `python scripts/gates/guard_no_qthread_compute.py` | `0` | `artifacts/phase4/gates_g4.txt` |
| `python scripts/gen_matrix.py --check` (`251` IDs verified, `116` `done`) | `0` | `artifacts/phase4/gates_g4.txt` |
| `python -m alembic upgrade head && downgrade base && upgrade head && current` (`0003 (head)`) | `0` | `artifacts/phase4/alembic_ladder.txt` |
| `pytest -q tests/test_reference_staleness.py` (`5 passed in 4.68s`) | `0` | `artifacts/phase4/subgates_g4abc.txt` |
| `pytest -q tests/gates/test_phase4_g4a_t1_t4.py` (`10 passed in 27.86s` — independent `G4a`) | `0` | `artifacts/phase4/subgates_g4abc.txt` |
| `pytest -q tests/gates/test_phase4_g4b_t5_t9.py` (`11 passed in 43.97s` — independent `G4b`) | `0` | `artifacts/phase4/subgates_g4abc.txt` |
| `pytest -q tests/gates/test_phase4_g4c_t10_t14.py` (`12 passed in 47.92s` — independent `G4c`) | `0` | `artifacts/phase4/subgates_g4abc.txt` |
| `pytest -q tests/gates tests/test_reference_staleness.py` (`154 passed in 186.51s`) | `0` | `artifacts/phase4/pytest_gates.txt` |
| `pytest -q --cov=energybaseline/core --cov=energybaseline/tests_catalog --cov-fail-under=60` + `coverage report --fail-under=80` (`179 passed`, `85.21%`) | `0` | `artifacts/phase4/pytest_full.txt` |
| `pytest -q tests/gui -p no:cacheprovider --cov=energybaseline/gui --cov-fail-under=52` (`5 passed`, `56.18%`) | `0` | `artifacts/phase4/pytest_gui.txt` |

---

## 4. `pytest -q` Summary & Coverage of Touched Modules

- **Full Test Suite (`pytest_full.txt`):** **`179 passed in 317.00s`** (`0 failed`, `0 skipped`, `0 xfailed`).
- **Combined `energybaseline/core` + `energybaseline/tests_catalog` Coverage:** **`85.21%`** (`10,293` statements, `1,522` missed — exceeds `>= 80%` CI gate and `>= 60%` baseline gate).
- **`energybaseline/gui` Coverage (`pytest_gui.txt`):** **`56.18%`** (`5 passed in 6.61s`, exceeds `>= 52%` CI gate; `175%` High-DPI `SetupPage` logical height `<= 411 px` preserved).
- **Per-Module Coverage on Phase 4 & Monitored Core Modules:**
  - `energybaseline/tests_catalog/monte_carlo.py`: **`98%`**
  - `energybaseline/tests_catalog/t7_structural_break_chow.py`: **`97%`**
  - `energybaseline/tests_catalog/t13_outlier_influence.py`: **`97%`**
  - `energybaseline/tests_catalog/t2_seasonality.py`: **`96%`**
  - `energybaseline/tests_catalog/t8_structural_break_quandt_andrews.py`: **`96%`**
  - `energybaseline/tests_catalog/t9_pettitt.py`: **`96%`**
  - `energybaseline/tests_catalog/__init__.py`: **`94%`**
  - `energybaseline/tests_catalog/t3_autocorrelation.py`: **`94%`**
  - `energybaseline/tests_catalog/ci_method.py`: **`92%`**
  - `energybaseline/tests_catalog/t1_stationarity.py`: **`92%`**
  - `energybaseline/tests_catalog/t12_little_mcar.py`: **`91%`**
  - `energybaseline/tests_catalog/t6_multicollinearity.py`: **`90%`**
  - `energybaseline/core/integrity.py`: **`89%`** (preserved — zero regression)
  - `energybaseline/core/pipeline.py`: **`89%`**
  - `energybaseline/tests_catalog/t5_heteroskedasticity.py`: **`88%`**
  - `energybaseline/tests_catalog/tc_assumptions.py`: **`88%`**
  - `energybaseline/core/cache.py`: **`87%`** (improved from `86%` in Phase 3)
  - `energybaseline/tests_catalog/t10_diebold_mariano.py`: **`87%`**
  - `energybaseline/tests_catalog/t11_nemenyi.py`: **`86%`**
  - `energybaseline/tests_catalog/r_reference_validator.py`: **`85%`**
  - `energybaseline/tests_catalog/t4_normality.py`: **`84%`**
  - `energybaseline/tests_catalog/t14_causal_group.py`: **`84%`**
  - `energybaseline/core/report.py`: **`83%`**

---

## 5. Artifacts (`E1` & `E2`) + SHA-256 Digests

| Class | Artifact Path | Producer Command / Purpose | SHA-256 |
|---|---|---|---|
| `E1` | `artifacts/phase4/gates_g4.txt` | Ruff, Pyflakes, 4 architecture guards, `gen_matrix.py --check`, invariant digests | `094a705070b51be20381588c893922b99c0a7c901f073b6f0fc01f7d0b89d9f4` |
| `E1` | `artifacts/phase4/alembic_ladder.txt` | Reversible migration `0001 -> 0003 -> base -> 0003` | `ab334f94c6ddf1f555fc2087e91e19f87b53c2f9638d7a3f438c1c44f12bf08a` |
| `E1` | `artifacts/phase4/subgates_g4abc.txt` | Independent sub-gates `G4a`, `G4b`, `G4c` + R staleness gate | `5099643826846bb325f6e6d7e3000d48c5aaf064d8ff334944f64b277feea7bf` |
| `E1` | `artifacts/phase4/pytest_gates.txt` | `pytest -q tests/gates tests/test_reference_staleness.py` (`154 passed`) | `bbd752268214cb2ea89ccb8abd13bd48f9f00b12c8adf380b13cb756df837b6a` |
| `E1` | `artifacts/phase4/pytest_full.txt` | Full suite (`179 passed`) + `85.21%` core/catalog coverage | `7b9d19b54cd72e7d64956d4652e3053bdd1ec0462f4dbdc1917b29eedb8e69bd` |
| `E1` | `artifacts/phase4/pytest_gui.txt` | Headless GUI suite (`5 passed`) + `56.18%` GUI coverage | `09c753204f50c6b6a44be14c43a6720ec1c796a8984ed3f8faf1dea51e60119d` |
| `E2` | `artifacts/phase4/evidence/e2_system_health.json` | `python -m energybaseline.cli system.health` | `76c7eaa46a6dbc4c4fde1dbbf6df99de75d720b7234a590b1772b882c5a41c6c` |
| `E2` | `artifacts/phase4/evidence/e2_system_sbom.json` | `python -m energybaseline.cli system.sbom` (CycloneDX 1.5) | `64a6b33c38eff89a425e2cf4989d6f1a72b288fef40ca7ac8646769b47f3017a` |
| `E2` | `artifacts/phase4/evidence/e2_r_references_check.json` | `python -m energybaseline.cli r-references-check` (`all_fresh: true`, `parity_ok: true`, `mismatches: []`) | `d1b67431939097edf3cfb927d37a9c7bd8c28bf9f35d4ef8d86e6afca517c65c` |
| `E2` | `artifacts/phase4/evidence/e2_test_contract_catalog.json` | `python -m energybaseline.cli test-catalog samples/monthly_building.csv --disable-test T7` (full `T1..T14` contract + `"not run (user)"` + `"not run (no group column)"` + `what_if_engine_calls_delta: 0`) | `bc2724c3ad78b35e0811bd8cbf3f5036cef4ecbf681d1906de456766f3cc470b` |
| `E2` | `artifacts/phase4/evidence/e2_monte_carlo_summary.json` | Monte Carlo Type I error & planted-effect power across all `T1..T14` (`all_type1_calibrated: true`, `all_power_achieved: true`) | `860140e68a3e7e3fc5a4ff9cb714431d532eb2fc51547f9e4ca145e5005ba421` |
| `E2` | `energybaseline/tests_catalog/r_references/chow.json` | R reference fixture (`T7` Chow F-test) | `20f6fdbfdc4e5489d70b61ae6544bffdb4cdd215a34191fbfd844b4f38dee065` |
| `E2` | `energybaseline/tests_catalog/r_references/quandt_andrews.json` | R reference fixture (`T8` Quandt-Andrews `sup-F` + bootstrap) | `04aab5247418c78f1e54c5ac7301376366f346fbf6704b84846c864e4221ff83` |
| `E2` | `energybaseline/tests_catalog/r_references/pettitt.json` | R reference fixture (`T9` Pettitt change-point) | `26cfa182a9ef6321ac46f229690f37ff2dbcd10455ea80072031b44fe4226166` |
| `E2` | `energybaseline/tests_catalog/r_references/diebold_mariano.json` | R reference fixture (`T10` HLN Diebold-Mariano) | `2e199687fb32082c1a97ebf8f1c0c0632ca2c619471ab94d1edbf8a96659dc02` |
| `E2` | `energybaseline/tests_catalog/r_references/mcs.json` | R reference fixture (`T10` Model Confidence Set) | `5cc7cf08e3e5db361c05eba057e155928f9d063e9a523006d81bdefbbed280eb` |
| `E2` | `energybaseline/tests_catalog/r_references/nemenyi.json` | R reference fixture (`T11` Friedman + Nemenyi) | `4f90abc7d4f9aa87a73b26edb474970062758772b1db9a9372e9c4a448a47d9d` |
| `E2` | `energybaseline/tests_catalog/r_references/little_mcar.json` | R reference fixture (`T12` Little's MCAR EM $\chi^2$) | `bd1544c09240164422e5043d30bb82fb9f97435f8654430ae6653f4961ad5194` |

---

## 6. `E3` (Windows & Linux Matrix + Nightly R Workflow): Logs, Timings & Reference Hardware Note

- **Automated 2-OS CI Matrix (`.github/workflows/ci.yml`):** Executes on both `windows-latest` (Python `3.11.9`, native `windows` Qt QPA + packaging/DPI/orphan/Authenticode evidence) and `ubuntu-latest` (Python `3.11.18`, `offscreen` Qt QPA), including dedicated execution of `tests/test_reference_staleness.py` and independent sub-gates `G4a`, `G4b`, and `G4c`.
- **Nightly Live-R Workflow (`.github/workflows/r-reference.yml`, `CI-3`):** Runs on `ubuntu-latest` with R `4.4.1`, executes all 7 `tests/reference/*.R` scripts from scratch, verifies parity against Python via `r_reference_validator.py` and `pytest tests/test_reference_staleness.py`, and automatically opens a GitHub issue via `actions/github-script@v7` if drift or staleness occurs.
- **Decision `D2 = B + A` Disclosure:** GitHub Actions `windows-latest` execution is labelled **CI, non-normative**; physical Windows 11 23H2 reference-hardware timing and visual sign-off remain scheduled for Phases 7–9.

---

## 7. Deltas in `deviations.md` / `assumptions.md` / `not_implemented.md`

- **`docs/deviations.md`:** Unchanged (`0` new deviations introduced in Phase 4).
- **`docs/assumptions.md`:** Unchanged; Phase 4 satisfies `D4 = B` (committed R reference JSON bundles with 14-day freshness enforcement in `test_reference_staleness.py` + nightly live-R verification in `.github/workflows/r-reference.yml`).
- **`docs/not_implemented.md`:** Unchanged (`LIF-4`, `SHL-8`, `F9-Prophet`, `PKG-3` remain transparently documented).
- **`docs/requirements.yaml` & `docs/requirements_matrix.md`:** Updated `27` Phase 4 requirement IDs (`AC-10..12`, `G-5`, `T1..T14`, `TC-1..8`, `CI-3`) to `status: done`, advancing total verified `done` IDs from `89` to **`116` / `251`**.

---

## 8. What Was Not Proven and Why

1. **Full Model Zoo (`F1–F10`) OOF cross-validation integration (`VAL-1..7`, `SC-1..10`):** Scheduled for **Phases 5 and 6**. In Phase 4, post-model tests (`T10` Diebold-Mariano / MCS and `T11` Friedman + Nemenyi) execute on any OOF prediction frame emitted by the pipeline or supplied directly to `run_full_test_catalog`; expanding the candidate model zoo and nested temporal CV splits belongs to Phases 5–6.
2. **Physical Windows 11 23H2 reference-hardware benchmarking (`D2`):** Automated CI runs on `windows-latest` virtual runners (**CI, non-normative**); physical reference-machine benchmarks are scheduled for Phases 7 and 9.
3. **Deterministic ReportLab PDF & `.ebproj` signed bundle export (`AC-25`, `AC-26`):** Phase 4 exports the complete `T1–T14` catalog (including `"not run (user)"` and `"naive"` labels) to HTML Section 15 and Excel `TestCatalog_*` sheets; ReportLab PDF and `.ebproj` packaging belong to Phases 8 and 9.

---

## 9. Open Risks and Their Effect on Phase 5 (Model Zoo `F1–F10` & Validation `VAL-1..7`)

1. **OOF Alignment Across Heterogeneous Model Horizons (`T10` / `T11` <- Phase 5):** Multi-step forecasting and lagged-feature models (`F6` LSTM, `F8` SARIMAX) can trim leading warm-up rows; Phase 5 OOF evaluation must align candidate predictions on an identical common OOF timestamp index before invoking `T10` (Diebold-Mariano / MCS) and `T11` (Friedman + Nemenyi).
2. **Autocorrelation-Aware Downstream Fold Sizing (`T1` / `T3` -> Phase 5):** When `T1` or `T3` flags high lag-1 persistence, Phase 5 blocked/rolling temporal cross-validation (`VAL-2`) should honor embargo/gap lengths scaled to the empirical autocorrelation decay to prevent fold-boundary leakage.
3. **14-Day R Reference Rolling Freshness (`TC-8` / `CI-3`):** Because `tests/test_reference_staleness.py` hard-fails when committed R reference JSON timestamps exceed 14 days without a refresh, the nightly `.github/workflows/r-reference.yml` workflow (`CI-3`) must remain active across Phases 5–9.
