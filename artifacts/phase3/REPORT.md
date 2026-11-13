# Phase 3 Verification & Gate G3 Report — VSG + TCP + Cache (Compliance Core)

- **Branch:** `arena/3611a52f-energybaseline`
- **Base Commit:** `4355cbf12f32a61c57cab7e42c9d97ff4c8bc5de` (Phase 2 Gate G2 verified)
- **Date:** `2026-11-12`
- **Scope:** `docs/EXECUTION_PLAN.md` §5 Phase 3 (`VSG-1..9`, `AUTO-1..6`, `TCP-1..14`, `PIPE-1..3`, `CACHE-1..2`, `G-6`, `AC-07`, `AC-08`, `AC-09`, `AC-23`, `AC-24`, `AC-29`, `AC-33`, Gate G3)

---

## 1. Phase ID, Commit Lineage & `git diff --stat`

- **Phase ID:** Phase 3 — VSG + TCP + Cache (compliance core)
- **Parent Commit SHA:** `4355cbf12f32a61c57cab7e42c9d97ff4c8bc5de`
- **Modified / Added Core, DB, GUI & Test Modules:**
  - `energybaseline/core/vsg.py` (new — `pre_test_profile`, `VariableSelectionGate`, `ComplianceGateError`)
  - `energybaseline/core/integrity.py` (new — non-disableable O(n) `IntegrityEngine`, `NON_DISABLEABLE_RULES`)
  - `energybaseline/core/provenance.py` (new — `ProvenanceRecord`, `StageArtifactReceipt`, SHA-256 hash-chained `ProvenanceLedger`)
  - `energybaseline/core/tcp.py` (new — `TestConfigPanel`, `adjust_p_values` 5 corrections, `T1..T14` test panels, scope matrix, expected-sign & gate-role editors, guard rails, `lock_preregistration` SHA-256, `compute_impact_preview`, 14-D `compute_target_characterization_profile`)
  - `energybaseline/core/cache.py` (new — 6-part `CacheKey`, `RawTestArtifact`, `EvaluatedTestResult` storing `p_raw` + `p_adj`, `ResultCache`, `CachedComplianceRunner` with zero-engine-call `what_if` & `decision_sensitivity_table`)
  - `energybaseline/core/auto.py` (updated — `AutoHiddenConstantError`, empirical-signal validation, `compute_auto_log_hash`, `AutoLogInspector`, isolated per-target merge)
  - `energybaseline/core/{quality,scoring,pipeline}.py` (updated — empirical-signal AUTO rules, per-target `AutoLog` isolation, `auto_log_hash`, `provenance`, `characterization`, `run_gated_inferential_stage`)
  - `energybaseline/core/telemetry.py` (new — `build_system_health`, `build_system_sbom` CycloneDX 1.5)
  - `energybaseline/db/models.py`, `alembic/versions/0003_phase3_vsg_tcp_cache.py`, `energybaseline/core/store.py` (updated/new — `vsg_states`, `tcp_configs`, `provenance_records`, `result_cache_entries` + SHA-256 tamper verification)
  - `energybaseline/gui/{pages_setup,advanced,main_window,commands,state}.py` & `energybaseline/cli.py` (updated — `ImpactPreviewPanel`, scrollable VSG & TCP tabs, UI-only `format_limitations_badges` `[post_hoc]`, `AutoPanel` SHA-256 hash + filter, `system.health`, `system.sbom`, Phase 3 CLI subcommands)
  - `tests/gates/{test_phase3_vsg_integrity_provenance,test_phase3_tcp_cache_whatif,test_phase3_auto_and_fuzz}.py` & `tests/gui/test_phase3_gui_vsg_tcp.py` (new — 20 new Gate G3 + Hypothesis + GUI test functions)

---

## 2. Requirement ID -> File / Function -> Test Traceability Table

| ID | File / Function | Verification Test(s) |
|---|---|---|
| `VSG-1` / `PIPE-1` / `AC-07` | `energybaseline/core/vsg.py::VariableSelectionGate`, `assert_accepted`; `energybaseline/core/cache.py::CachedComplianceRunner.execute_tests`; `energybaseline/core/pipeline.py::run_gated_inferential_stage`; `energybaseline/gui/pages_setup.py::AnalysisPage.start` | `tests/gates/test_phase3_vsg_integrity_provenance.py::test_pipe1_blocks_inferential_tests_before_vsg_and_tcp_acceptance`, `tests/gui/test_phase3_gui_vsg_tcp.py::test_gui_impact_preview_vsg_tcp_whatif_and_post_hoc_ui_badge` |
| `VSG-2` | `energybaseline/core/vsg.py::pre_test_profile` (missing %, unique count, variance, constant/near-constant, leakage suspicion, availability, mini-histogram) | `tests/gates/test_phase3_vsg_integrity_provenance.py::test_vsg_pre_test_profile_mi_degree_day_and_vif_grouping`, `tests/gates/test_phase3_auto_and_fuzz.py::test_hypothesis_fuzz_vsg_and_integrity_invariants` |
| `VSG-3` | `energybaseline/gui/pages_setup.py::ImpactPreviewPanel`; `energybaseline/core/tcp.py::compute_impact_preview` | `tests/gates/test_phase3_tcp_cache_whatif.py::test_tcp_scope_matrix_guard_rails_lock_immutability_and_impact_preview`, `tests/gui/test_phase3_gui_vsg_tcp.py::test_gui_impact_preview_vsg_tcp_whatif_and_post_hoc_ui_badge` |
| `VSG-4` | `energybaseline/core/vsg.py::VariableSelectionGate.bulk_set_disposition`, `apply_preset`, `copy_target_decisions`, `excluded_descriptive_profiles` | `tests/gates/test_phase3_vsg_integrity_provenance.py::test_vsg_bulk_presets_cross_target_copy_and_excluded_profiles` |
| `VSG-5` / `AC-08` | `energybaseline/core/integrity.py::IntegrityEngine`, `run_integrity_checks` (non-disableable O(n) checks: `TARGET_AS_PREDICTOR`, `TARGET_LEAKAGE_MT2`, `TIME_ORDER_VIOLATION`, `DST_DUPLICATE_TIMESTAMPS`); `energybaseline/core/vsg.py::_compute_vif_map` | `tests/gates/test_phase3_vsg_integrity_provenance.py::test_integrity_rules_cannot_be_disabled_and_catch_all_four_classes`, `test_integrity_linear_time_complexity_on_large_frame` |
| `VSG-6` / `PIPE-2` | `energybaseline/core/provenance.py::ProvenanceRecord`, `ProvenanceLedger` (`phase` strictly in `pre_test \| post_test`); `energybaseline/gui/pages_setup.py::format_limitations_badges` (UI-only `[post_hoc]` Limitations badge) | `tests/gates/test_phase3_vsg_integrity_provenance.py::test_provenance_ledger_hash_chain_and_zero_post_hoc_outside_gui`, `tests/gui/test_phase3_gui_vsg_tcp.py::test_gui_impact_preview_vsg_tcp_whatif_and_post_hoc_ui_badge` |
| `VSG-7` / `VSG-8` / `VSG-9` | `energybaseline/core/vsg.py::_mutual_information_1d`, `VariableSelectionGate._resolve_target_auto`, `override_variable` (mandatory audit reason) | `tests/gates/test_phase3_vsg_integrity_provenance.py::test_vsg_pre_test_profile_mi_degree_day_and_vif_grouping`, `test_vsg_bulk_presets_cross_target_copy_and_excluded_profiles` |
| `TCP-1` / `PIPE-3` | `energybaseline/core/tcp.py::TestConfigPanel.set_alpha` (`0.001..0.20`, presets, warning `> 0.10`), `set_confidence` (`80/90/95/99/custom`) | `tests/gates/test_phase3_tcp_cache_whatif.py::test_tcp_alpha_bounds_presets_warning_and_confidence_modes`, `tests/gates/test_phase3_auto_and_fuzz.py::test_hypothesis_fuzz_tcp_guard_rails_and_zero_call_what_if` |
| `TCP-2` | `energybaseline/core/tcp.py::adjust_p_values` (`none`, `bonferroni`, `holm`, `benjamini_hochberg`, `benjamini_yekutieli` storing both `p_raw` and `p_adj`) | `tests/gates/test_phase3_tcp_cache_whatif.py::test_tcp_five_multiple_testing_corrections_exact_values_and_dual_storage`, `tests/gates/test_phase3_auto_and_fuzz.py::test_hypothesis_fuzz_tcp_five_corrections_monotonicity_and_dominance` |
| `TCP-3` / `TCP-4` / `TCP-5` | `energybaseline/core/tcp.py::TestConfigPanel` (`family_definition`, `decision_rule`, `build_scope_matrix`, `set_expected_sign`, `configure_test_panel`, guard rails, `lock_preregistration` SHA-256) | `tests/gates/test_phase3_tcp_cache_whatif.py::test_tcp_scope_matrix_guard_rails_lock_immutability_and_impact_preview` |
| `TCP-6..14` / `AC-29` | `energybaseline/core/tcp.py::compute_target_characterization_profile` (14-dimension pre-modeling characterization incl. seasonality, ACF/PACF, ARCH, Chow/Quandt-Andrews/Pettitt breaks, piecewise nonlinearity, lag XCF, modes, SNR/entropy, persisted JSON manifest) | `tests/gates/test_phase3_tcp_cache_whatif.py::test_tcp_14d_characterization_and_sqlite_persistence_tamper_guards` |
| `CACHE-1` / `AC-23` | `energybaseline/core/cache.py::CacheKey`, `ResultCache`, `CachedComplianceRunner.run_compliance_stage_dag` (6-part deterministic key + selective stage/target invalidation) | `tests/gates/test_phase3_tcp_cache_whatif.py::test_cache_selective_invalidation_and_zero_recomputation_what_if` |
| `CACHE-2` / `AC-24` | `energybaseline/core/cache.py::CachedComplianceRunner.what_if`, `decision_sensitivity_table` (0 engine calls verified via `engine_call_count`) | `tests/gates/test_phase3_tcp_cache_whatif.py::test_cache_selective_invalidation_and_zero_recomputation_what_if`, `tests/gates/test_phase3_auto_and_fuzz.py::test_hypothesis_fuzz_tcp_guard_rails_and_zero_call_what_if` |
| `AUTO-1..6` / `G-6` / `AC-09` / `AC-33` | `energybaseline/core/auto.py::AutoLog`, `AutoHiddenConstantError`, `compute_auto_log_hash`, `AutoLogInspector`; `energybaseline/gui/advanced.py::AutoPanel` | `tests/gates/test_phase3_auto_and_fuzz.py::test_auto_log_rejects_hidden_constants_and_adapts_to_data_signals`, `test_pipeline_twice_produces_byte_identical_auto_log_hash` |

---

## 3. Commands Executed & Raw Output Paths (`E1`)

| Command | Exit Code | Raw Output Artifact |
|---|---|---|
| `python -m ruff check . && python -m ruff format --check .` | `0` | `artifacts/phase3/gates_g3.txt` |
| `python -m pyflakes energybaseline` | `0` | `artifacts/phase3/gates_g3.txt` |
| `python scripts/gates/guard_web_free.py` | `0` | `artifacts/phase3/gates_g3.txt` |
| `python scripts/gates/guard_env_readers.py` | `0` | `artifacts/phase3/gates_g3.txt` |
| `python scripts/gates/guard_tests_integrity.py` | `0` | `artifacts/phase3/gates_g3.txt` |
| `python scripts/gates/guard_no_qthread_compute.py` | `0` | `artifacts/phase3/gates_g3.txt` |
| `python scripts/gen_matrix.py --check` (`251` IDs verified, `89` `done`) | `0` | `artifacts/phase3/gates_g3.txt` |
| 4 repository hygiene assertions (`git ls-files` web/pycache, `weasyprint`, `launcher.py`) | `0` | `artifacts/phase3/gates_g3.txt` |
| `python -m alembic upgrade head && downgrade base && upgrade head && current` (`0003 (head)`) | `0` | `artifacts/phase3/alembic_ladder.txt` |
| `python -m pytest -q tests/gates` (`116 passed in 59.56s`) | `0` | `artifacts/phase3/pytest_gates.txt` |
| `python -m pytest -q --cov=energybaseline/core --cov-fail-under=60` + `coverage report --fail-under=80` | `0` | `artifacts/phase3/pytest_full.txt` |
| `python -m pytest -q tests/gui -p no:cacheprovider --cov=energybaseline/gui --cov-fail-under=52` | `0` | `artifacts/phase3/pytest_gui.txt` |

---

## 4. `pytest -q` Summary & Coverage of Touched Modules

- **Full Test Suite (`pytest_full.txt`):** **`141 passed in 143.45s`** (`0 failed`, `0 skipped`, `0 xfailed`).
- **`energybaseline/core` Total Coverage:** **`82.87%`** (`7707` statements, `1320` missed — exceeds `S5` `>= 80%` target, CI `>= 80%` gate, and baseline `>= 60%` gate).
- **`energybaseline/gui` Total Coverage (`pytest_gui.txt`):** **`56.18%`** (`5 passed in 6.56s`, exceeds `>= 52%` CI gate).
- **Per-Module Coverage on Touched / New Phase 3 Modules:**
  - `energybaseline/core/telemetry.py`: **`98%`**
  - `energybaseline/core/provenance.py`: **`93%`**
  - `energybaseline/core/auto.py`: **`91%`**
  - `energybaseline/core/vsg.py`: **`91%`**
  - `energybaseline/core/tcp.py`: **`90%`**
  - `energybaseline/core/integrity.py`: **`89%`**
  - `energybaseline/core/pipeline.py`: **`89%`**
  - `energybaseline/core/scoring.py`: **`89%`**
  - `energybaseline/core/store.py`: **`88%`**
  - `energybaseline/core/cache.py`: **`86%`**
  - `energybaseline/gui/commands.py`: **`88%`**
  - `energybaseline/gui/main_window.py`: **`84%`**
  - `energybaseline/gui/pages_setup.py`: **`68%`**

---

## 5. Artifacts (`E1` & `E2`) + SHA-256 Digests

| Class | Artifact Path | Producer Command / Purpose | SHA-256 |
|---|---|---|---|
| `E1` | `artifacts/phase3/gates_g3.txt` | Static, AST, matrix, and hygiene gates | `808569197b0b7e7019daba313979957175c84bc68d8ab36e8bc21dc68434dbc4` |
| `E1` | `artifacts/phase3/alembic_ladder.txt` | Reversible migration `0001 -> 0003 -> base -> 0003` | `710dec15d99ceb659b295fa02e5bd488c6137b9aed231815da046d3ae437d551` |
| `E1` | `artifacts/phase3/pytest_gates.txt` | `pytest -q tests/gates` (`116 passed`) | `4a40ec52cd05e2b218be3fecec3c2381e54c48b6ffb249ddf6a790738253cb1c` |
| `E1` | `artifacts/phase3/pytest_full.txt` | Full suite + `82.87%` core coverage | `9957cc674231948c6a8665e7820da227eccae1a522e8a4b40e9d976319f18d27` |
| `E1` | `artifacts/phase3/pytest_gui.txt` | Headless GUI suite + `56.18%` GUI coverage | `1d982d8f4a96334ff39773e6b78cb08bdd46b547a396a96223f8daf911995052` |
| `E2` | `artifacts/phase3/evidence/e2_system_health.json` | `python -m energybaseline.cli system.health` | `56b080430e6bfafe4ccdd3a1cab6bb45da3d749a87578626d03b7daad77c9bcf` |
| `E2` | `artifacts/phase3/evidence/e2_system_sbom.json` | `python -m energybaseline.cli system.sbom` (`162` CycloneDX 1.5 components) | `64a6b33c38eff89a425e2cf4989d6f1a72b288fef40ca7ac8646769b47f3017a` |
| `E2` | `artifacts/phase3/evidence/e2_auto_log_hash.json` | `python -m energybaseline.cli auto-log-hash samples/monthly_building.csv` (`byte_identical: true`, hash `f814ac7a5ba212ef80ad061bbf191ef236e3c74cd6b60a6855aac0bfb68892b4`) | `ffdc726bb328f9a29b958ac6fce558b68926964d7d84c3d5ca05be283e89760b` |
| `E2` | `artifacts/phase3/evidence/e2_vsg_inspect.json` | `python -m energybaseline.cli vsg-inspect samples/monthly_building.csv --accept` | `dbb5d3c3f301ce2dc08a79a88f5e7c9c31b70aabcf4339863e3fe14a7b835af3` |
| `E2` | `artifacts/phase3/evidence/e2_tcp_lock.json` | `python -m energybaseline.cli tcp-lock samples/monthly_building.csv` (lock `5f9032ba287826132eb31d940dab64fd8c0ab931f0ec22f7ef0aaa639b34bd93` + 14-D profile) | `02447a715e9e85a0fc3421a2312223895934b2c80ada78fa76ea236589174d67` |
| `E2` | `artifacts/phase3/evidence/e2_what_if_sensitivity.json` | `python -m energybaseline.cli what-if samples/monthly_building.csv --alpha 0.01 --correction bonferroni` (`engine_calls_delta: 0`) | `677d5b454933c42765e282f65bc097ce89e8a0e5e96308b22f613d69f9407d70` |

---

## 6. `E3` (Windows & Linux Matrix): Logs, Timings & Reference Hardware Note

- **Automated 2-OS CI Matrix (`.github/workflows/ci.yml`):** Executes on both `windows-latest` (Python `3.11.9`, native `windows` Qt QPA + `installer_smoke.ps1`, `lif_orphans.ps1`, `startup_time.ps1`, `dpi_matrix.ps1`, Authenticode smoke) and `ubuntu-latest` (Python `3.11.18`, `offscreen` Qt QPA).
- **Local High-DPI Pre-Flight (`100%`, `125%`, `150%`, `175%`):** Verified with new scrollable VSG/TCP tabs and `ImpactPreviewPanel`; at `175%` (`QT_SCALE_FACTOR=1.75`), `frame_width=435`, `frame_height=411`, `fits_width=true`, `fits_height=true`, `orphan_pids=[]`.
- **Decision `D2 = B + A` Disclosure:** GitHub Actions `windows-latest` execution is labelled **CI, non-normative**; physical Windows 11 23H2 reference-hardware benchmarking and visual inspection remain release-level requirements in Phases 7–9.

---

## 7. Deltas in `deviations.md` / `assumptions.md` / `not_implemented.md`

- **`docs/deviations.md`:** Unchanged (`0` new architectural or specification deviations introduced in Phase 3).
- **`docs/assumptions.md`:** Unchanged; Phase 3 implements both the Test Configuration Panel (`TCP-1..5`, scope matrix, expected-sign/gate-role editors, SHA-256 pre-registration lock) and the 14-dimension Target Characterization Profile (`TCP-1..14`) cleanly in `energybaseline/core/tcp.py`.
- **`docs/not_implemented.md`:** Unchanged (`LIF-4`, `SHL-8`, `F9-Prophet`, `PKG-3` remain transparently documented).
- **`docs/requirements.yaml` & `docs/requirements_matrix.md`:** Updated (`89` `done` across `251` tracked IDs, including `VSG-1..9`, `AUTO-1..6`, `TCP-1..14`, `PIPE-1`, `PIPE-2`, `CACHE-1`, `CACHE-2`, `G-6`, `AC-07`, `AC-08`, `AC-09`, `AC-29`).

---

## 8. What Was Not Proven and Why

1. **Full `T1–T14` statistical test catalog against R golden reference fixtures (`AC-10..AC-12`):** Scheduled for **Phase 4**. Phase 3 implements lightweight deterministic screening primitives in `energybaseline/core/cache.py` to exercise the 6-part `CacheKey`, dual `(p_raw, p_adj)` storage, and 0-engine-call `what_if` / sensitivity machinery; Phase 4 replaces/expands the test catalog with full assumption-checked parametric + non-parametric tests verified against R fixtures.
2. **Physical Windows 11 23H2 reference-machine latency benchmarks (`AC-23` / `D2`):** Automated CI runs on `windows-latest` virtual runners (**CI, non-normative**); physical reference-hardware timing sign-off is scheduled for Phase 7/9.
3. **Full ISO 50006 PDF / `.ebproj` audit-bundle export of Limitations & Provenance (`AC-25`, `AC-33`):** Core provenance ledger, SHA-256 pre-registration lock, deterministic `auto_log_hash`, and UI `[post_hoc]` Limitations badges are complete and verified in Phase 3; ReportLab PDF rendering and `.ebproj` ZIP packaging are scheduled for Phases 8 and 9.

---

## 9. Open Risks and Their Effect on Phase 4 (Test Catalog `T1–T14`)

1. **Phase 4 Catalog Plug-In Contract (`cache.py` -> `tests_catalog`):** Phase 4 statistical tests (`T1–T14`, including structural breaks, cointegration, seasonal unit roots, and subgroup invariance) must emit `RawTestArtifact`-compatible raw p-values, test statistics, effect sizes, and confidence bounds so `ResultCache` and zero-recomputation `what_if` remain intact without recomputing test statistics on `alpha`/correction edits.
2. **Small-Sample / Degenerate Series Edge Cases in `T1–T14`:** Hypothesis fuzzing in Phase 3 surfaced constant/high-missing/short-series inputs cleanly; Phase 4 tests must preserve explicit assumption checks and non-parametric fallbacks (`G-5`) without raising unhandled numerical exceptions.
3. **High-DPI Vertical Budget on `SetupPage`:** Adding Phase 4 per-test diagnostic visualizations must continue using scrollable containers or secondary dialogs so `175%` High-DPI window height (`<= 411 px` logical frame height) remains within bounds.
