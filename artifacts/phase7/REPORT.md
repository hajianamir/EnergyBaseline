# Phase 7 — UQ, Drift, Prediction & Monitoring, Causal M&V (Gate G7)

## 1. Verdict and source identity

**Executor verification: PASS locally and on both E3 operating systems. Independent reviewer acceptance is pending.**

- Branch: `arena/3611a52f-energybaseline`; `main` and reviewer branches untouched; no executor PR opened.
- Final executable-source commit: `074af513e8a07f2ae3ad0aaa7d7a5b52c70a969a` (hash-precision follow-up to `d5bde17d8383fb4aa29fe6285cb2c9dd231ec447`).
- SHA-256 of the Git commit object bytes (`git cat-file commit HEAD`): `ccb84fe85ef9896e73157802684b6699bde7a2c82738a17e14fa36358cbcfa64`.
- Source delta from the inherited reviewer merge `5b6c7f313f19dad2276f639897d8b780f5011b83`: **34 files, 2,337 insertions, 101 deletions**. Full `git diff --stat`: `source_diff_stat.txt`.
- This report/evidence commit does not change executable source; final evidence-commit identity is in Git history, avoiding a self-referential commit hash in the report.
- Requirements matrix: **176 / 251 done**, advancing **20** requested Phase 7 IDs. Every newly done ID has executable tests. Other IDs, including multi-target extensions not included in this handoff's deliverable list, were not advanced without evidence.

### Phase 6 remaining verification

Phase 6 E1/E2/REPORT files remain committed in the inherited branch. Its matrix recorded **156 / 251 done** before this phase. The owner's handoff supplies the independent reviewer Gate G6 verdict (181 passes plus two environmental torch/LSTM cases); that reviewer run is not represented as a new executor run. The executor's prior Phase 6 two-OS run `43515001336` is independently confirmed in `phase6_e3_confirmation.json`; Gate G6 also passed on both operating systems in the Phase 7 run. The current environment has the locked torch dependency and the full executor suite has no skips. No Phase 6 merge into `main` was performed, respecting the explicit untouched-main/no-executor-PR restriction.

## 2. Delivered behavior

- MAPIE 1.6 split, cross, bootstrap, quantile, EnbPI and ACI adapters; real backends, not residual-quantile substitutes. Chronological training/calibration/holdout separation is exposed explicitly. ACI calls **`adapt_conformal_inference` before `update`**; `update` alone does not adapt alpha in MAPIE 1.6. Outcomes are observed only after intervals are emitted. External/locked model coefficients are not changed.
- OLS/HAC and quantile intervals cross-checked with conformal bounds; empirical coverage, Kupiec unconditional coverage and Christoffersen independence/conditional coverage diagnostics. An intentionally under-covering small split-calibration partition is reported as failure by the CLI, never repaired using holdout labels.
- Bartlett-tapered serial covariance propagation, calibration-mean uncertainty, independent measurement and fully correlated static-factor uncertainty; 90%/95% user-selectable FSU, explicitly undefined for zero savings.
- PSI, two-sample KS/Wasserstein, two-sided Page-Hinkley, Pettitt, decision-interval CUSUM and variance-shift diagnostics; rolling interval miscoverage. Engineering advisories distinguish possible improvement from breakdown and condition covariate shifts on residual/domain evidence, avoiding a seasonal-loading-only re-baselining recommendation.
- Strict API/CLI reporting rejects schema, units, cadence, timestamps and malformed numeric inputs without resampling; routine predictions and documented static-factor adjustments are separate. EnPI/savings, propagated period/cumulative bands, CUSUM and exact/asymptotic EWMA limits are available. Append-only hash-chained batches preserve cumulative/control-chart state, reject overlaps/duplicates and bind baseline version/hash plus full-precision input hash.
- Desktop snapshot persistence now requires a verified lock and original input hash, hashes stored payload bytes and verifies the historical lock on load. Legacy unbound snapshots require re-validation. Existing desktop ingestion UX is not represented as the new strict interface.
- Causal balance, propensity overlap, pre-trends, entropy-regularized RBF balancing, DID/HAC intervals, placebo refutation and **installed EconML** estimation/refutation. Results remain **possible savings / non-routine event** with explicit observational assumptions. Failed design diagnostics suppress the reported effect.
- Source-grep false positive resolved via reversible migration 0004: encrypted connection storage renamed to `protected_payload`; existing ciphertext is preserved byte-for-byte.

## 3. Requirement → file → function → executable test

All Phase 7 gate tests below are in `tests/gates/test_phase7_uq_monitoring_causal.py`; GUI tests are in `tests/gui/test_phase7_gui_snapshot.py`. Existing tests were neither edited nor deleted.

| ID | File | Entry point | Test |
|---|---|---|---|
| AC-16 | `energybaseline/uq/__init__.py` | `run_uq_catalog` | `test_uq3_ac16_serial_holdout_calibration` |
| AC-32 | `energybaseline/monitoring/drift.py` | `run_drift_monitor` | `test_dr1_dr2_dr3_dr4_dr5_ac32_readonly_advisories`, `test_independent_stable_drift_and_injected_mean_variance_shifts` |
| UQ-1 | `energybaseline/uq/conformal.py` | `ConformalIntervals.fit / predict / observe` | `test_uq1_real_mapie_backends` |
| UQ-2 | `energybaseline/uq/parametric.py` | `ols_hac_intervals / quantile_intervals / crosscheck_intervals` | `test_uq2_parametric_quantile_conformal_crosscheck` |
| UQ-3 | `energybaseline/uq/calibration.py` | `interval_diagnostics` | `test_uq3_ac16_serial_holdout_calibration` |
| UQ-4 | `energybaseline/uq/savings_uncertainty.py` | `savings_uncertainty` | `test_uq4_uq5_cumulative_serial_uncertainty_and_fsu` |
| UQ-5 | `energybaseline/uq/fsu.py` | `fractional_savings_uncertainty` | `test_uq4_uq5_cumulative_serial_uncertainty_and_fsu` |
| PM-1 | `energybaseline/monitoring/prediction.py` | `validate_reporting` | `test_pm1_reporting_fuzz_corpus` |
| PM-2 | `energybaseline/monitoring/prediction.py` | `predict_reporting / cusum_chart / ewma_chart` | `test_pm2_pm3_pm4_pm5_pm6_dom3_prediction` |
| PM-3 | `energybaseline/monitoring/prediction.py` | `predict_reporting / cusum_chart / ewma_chart` | `test_pm2_pm3_pm4_pm5_pm6_dom3_prediction` |
| PM-4 | `energybaseline/monitoring/prediction.py` | `predict_reporting / cusum_chart / ewma_chart` | `test_pm2_pm3_pm4_pm5_pm6_dom3_prediction` |
| PM-5 | `energybaseline/monitoring/prediction.py` | `predict_reporting / cusum_chart / ewma_chart` | `test_pm2_pm3_pm4_pm5_pm6_dom3_prediction` |
| PM-6 | `energybaseline/monitoring/prediction.py` | `predict_reporting / cusum_chart / ewma_chart` | `test_pm2_pm3_pm4_pm5_pm6_dom3_prediction` |
| PM-7 | `energybaseline/monitoring/prediction.py` | `MonitoringHistory.append` | `test_pm7_snapshot_baseline_input_binding_and_integrity`, `test_incremental_charts_savings_and_snapshot_no_model_mutation` |
| DR-1 | `energybaseline/monitoring/drift.py` | `run_drift_monitor` | `test_dr1_dr2_dr3_dr4_dr5_ac32_readonly_advisories`, `test_independent_stable_drift_and_injected_mean_variance_shifts` |
| DR-2 | `energybaseline/monitoring/drift.py` | `run_drift_monitor` | `test_dr1_dr2_dr3_dr4_dr5_ac32_readonly_advisories`, `test_independent_stable_drift_and_injected_mean_variance_shifts` |
| DR-3 | `energybaseline/monitoring/drift.py` | `run_drift_monitor` | `test_dr1_dr2_dr3_dr4_dr5_ac32_readonly_advisories`, `test_independent_stable_drift_and_injected_mean_variance_shifts` |
| DR-4 | `energybaseline/monitoring/drift.py` | `run_drift_monitor` | `test_dr1_dr2_dr3_dr4_dr5_ac32_readonly_advisories`, `test_independent_stable_drift_and_injected_mean_variance_shifts` |
| DR-5 | `energybaseline/monitoring/drift.py` | `run_drift_monitor` | `test_dr1_dr2_dr3_dr4_dr5_ac32_readonly_advisories`, `test_independent_stable_drift_and_injected_mean_variance_shifts` |
| DOM-3 | `energybaseline/monitoring/adjustments.py` | `NonRoutineAdjustment / apply_adjustments` | `test_pm2_pm3_pm4_pm5_pm6_dom3_prediction` |
| T14 extension | `energybaseline/causal/mv.py` | `run_causal_mv / kernel_balance / optional_backend_effect` | `test_t14_balance_overlap_trends_effect_refutation`, `test_t14_optional_backend_honest_status` |
| Lock snapshot | `energybaseline/lock/baseline_lock.py`, `core/store.py` | `monitoring_snapshot / save_snapshot / load_snapshot` | `test_pm7_snapshot_baseline_input_binding_and_integrity`, frozen `test_store_lock_monitor_and_report`, two GUI tests |
| Input-hash precision | `energybaseline/monitoring/prediction.py` | `frame_input_hash` | `test_snapshot_hash_distinguishes_float_ulps_and_timestamp_nanoseconds` |
| Encrypted-storage migration | `alembic/versions/0004_phase7_protected_payload.py` | `upgrade / downgrade` | `test_connection_payload_encrypted_and_migration_preserves_bytes` |

## 4. Canonical G7 command evidence

All canonical G7 commands exited **0** on final source. Local invocation uses `.venv/bin/python`, `QT_QPA_PLATFORM=offscreen` and sandbox-only `LD_LIBRARY_PATH=/tmp/qstub`; these do not change the product or CI environment.

| Command / check | Result | Raw output |
|---|---|---|
| `python -m pytest -q` | **243 passed**, 272.54s | `e1_pytest_full.txt` |
| `python -m pytest -q tests/gates` | **208 passed**, 242.36s | `e1_pytest_gates.txt` |
| G7 targeted tests | **25 passed**, 53.71s | `e1_targeted.txt` |
| Canonical full coverage invocation below | **243 passed**, 403.16s; **86.01%** >= 60% | `e1_coverage.txt` |
| `python -m pytest -q tests/gui -p no:cacheprovider` (with additional GUI coverage) | **10 passed**, 14.81s; **65.69%** >= 56% | `e1_pytest_gui.txt` |
| Ruff check / format check; Pyflakes | PASS | `e1_static_guards_migrations.txt` |
| All four architecture/integrity guards | PASS; four frozen baseline files verified | same |
| `python scripts/gen_matrix.py --check` | PASS; 176 / 251 done | same |
| All eight canonical `test -z` source/tracked-file checks | PASS | same |
| `alembic upgrade head`, `downgrade base`, `upgrade head` | PASS, including reversible 0004 | same |
| R reference parity/freshness | PASS; 2.063 days < 14, parity true | same + `r_references_check.json` |
| Hash-locked install, dependency consistency | PASS | CI install steps; local `pip check` in static log |
| Vulnerability audit | Both E3 audits PASS; local pinned-closure audit: no known vulnerabilities | E3 step records + static log |

```bash
python -m pytest -q --cov=energybaseline/core --cov=energybaseline/models --cov=energybaseline/scoring --cov=energybaseline/validation --cov=energybaseline/uq --cov=energybaseline/monitoring --cov=energybaseline/causal --cov-fail-under=60
```

The new `uq`, `monitoring`, `causal` packages together cover **533 / 577 statements (92%)**. The locked license/baseline-test manifest hashes remain:

- `LICENSE`: `1c633e61e328b34b883571ae25a92cf6a390e6f2752871331545d70a5b553afe`
- `tests/.baseline.sha256`: `6f3abde78940ac71c104a32305c7a69bc2a9c90a09f6660a332de0eac467f3dc`

An initial parallel full-coverage attempt was killed under sandbox memory pressure; its isolated rerun passed. A supplemental final full-resolver `pip-audit` attempt encountered an interrupted 285.8 MB dependency download; the complete exact-pinned closure was then audited successfully using `--disable-pip --no-deps`, without re-resolving/downloading wheels. Both standard E3 `pip-audit -r requirements.lock` steps passed. The raw local failure and successful follow-up are retained, not concealed.

## 5. Calibration and rejection criteria

1000-point out-of-sample synthetic AR(1) residual series, rho=0.3, fixed training prefix and seed 2026; both methods operate predict-before-observe with gamma=0.005 for ACI. No universal coverage claim is made for serial dependence or arbitrary distribution shifts.

| Method | Nominal | Observed | Kupiec p | Christoffersen independence p | Conditional p |
|---|---|---|---|---|---|
| aci | 80% | 80.4% | 0.7512 | 0.0608 | 0.1639 |
| aci | 95% | 95.4% | 0.5566 | 0.5478 | 0.7022 |
| enbpi | 80% | 81.7% | 0.1742 | 0.0797 | 0.0856 |
| enbpi | 95% | 95.5% | 0.4609 | 0.5015 | 0.6079 |

All intervals are within ±5 pp and all reported backtests exceed 0.05 on this evidence series. Additional tests detect clustered misses even with correct marginal coverage, detect shifted covariates/residual mean/variance, and keep ten independent stationary driver/residual samples quiet. A future-outcome injection cannot change prior interval outputs. ACI alpha changes are tested separately from estimator immutability.

- Reporting rejection corpus: **115 malformed cases** in the gate test (15 structural/type cases plus 100 generated missing/infinite/timestamp cases), all rejected deterministically; valid input and unit/granularity rejection are checked separately. `reporting_fuzz.json` is a product-generated representative 10-case E2 corpus: 10/10 rejected, 120 valid rows accepted.
- Snapshot tests verify lock version/hash and input hash, modified artifact rejection, chain-tamper rejection, batch overlap rejection and one-ULP/one-nanosecond input distinctions. Two batches match one-shot cumulative savings/bands/charts.
- Drift receives numeric arrays and accepted TCP, not a mutable estimator; returned advisories never trigger model updates. Tests verify unchanged coefficients and lock integrity.
- SQL read-only validation continues through `sqlglot.parse`; no regex SQL validator was introduced.
- Connection data remain encrypted. Persistence/export guards reject sensitive keys/connection material. Read-only ZIP inspection accepts safe JSON and rejects deliberately tainted JSON; this is **not** an assertion that Phase 8/9 project packaging has been implemented. No user project ZIP is generated here.
- Inferential gateways require the real accepted VSG/TCP. Pre-gate calls explicitly fail.

## 6. E2 artifacts and reproducibility

Generated by `python scripts/evidence/phase7.py --out-dir artifacts/phase7` using product APIs; raw output `e2_generation.txt`. Health reports `ok=true`; monitoring output is real model prediction, not a mock; baseline snapshot includes verified version/hash and input hash. Disposable estimator artifacts are outside Git per the existing ignore convention. The script is the reproducible entry point, not those temporary model paths.

| Artifact | SHA-256 |
|---|---|
| `system_health.json` | `76c7eaa46a6dbc4c4fde1dbbf6df99de75d720b7234a590b1772b882c5a41c6c` |
| `sbom.json` | `ca0e8226cfd07382221d42cde3d3096b0ec6cf56d5a1a283fd2c4716bb93393b` |
| `uq_calibration.json` | `1e9e5e1ec03bb546176d78ed2c5ad8f234c6a58328cf715e93e946bc45d41312` |
| `parametric_crosscheck.json` | `3dbe760af7ecff6d47e675993255c01ea41e93273f855e59d3ec91401848629d` |
| `fsu.json` | `914f02eaa6ca7bc8b166e7b3aede00b36de57867b76b01c954d59605b19491f0` |
| `drift_monitor.json` | `a68c77233a3f1810edc88607ff12d9b3a7a29f02c10a779eff2b6b42182161cd` |
| `prediction.json` | `a8fbe2c2ef01ec55b35fcab14d53c64ef350d0df3c694b663ae0867a312289dc` |
| `locked_baseline_snapshot.json` | `ff0dcd72ea5c7c4603e608b02f1fe1d9a553dcfc48da40b4be4745bd241f98a7` |
| `monitoring_audit.json` | `22cffd5e68339e68f9d4861e6719f13e0cbfbf6685be4eea058be4672526735d` |
| `reporting_fuzz.json` | `683270ba669464216a15b8ddb2a54bfc0124eae66185b2d727256764ad84d71b` |
| `causal_mv.json` | `770859fddc7e7bcae434cd234bcd25c950d0e17103fdf13e81cffedebdde7f71` |
| `r_references_check.json` | `f597939084965466db11ff4c73d15f1993bd2ac118dd97904242f433e0f4a66e` |
| `e3_ci_run.json` | `3d0023d3c4f748dc869993bc75930d61efe7000d552deeac8822b34f73a9e32b` |

`manifest.json` covers all evidence files (including this report), excludes itself to avoid self-reference, and records final source identity and UQ tolerance status.

## 7. E3: both operating systems green

Run **43537426337**, source **074af513e8a07f2ae3ad0aaa7d7a5b52c70a969a**:
https://github.com/hajianamir/EnergyBaseline/actions/runs/43537426337

| Runner | Job ID | Result | Elapsed |
|---|---|---|---|
| windows-latest | 132269697606 | **success** | **21m12s** |
| ubuntu-latest | 132269697999 | **success** | **21m15s** |

Gate G1/G4/G5/G6/G7, full coverage, GUI and audit steps passed on both. Windows packaging, lifecycle, installer, startup/DPI and self-signed signing smoke checks also passed; those D2 measurements are explicitly CI/non-normative. E3 API job/step records: `e3_ci_run.json`; raw live step-status transcript: `e3_watch.txt`. Full job-log ZIP retrieval was unavailable because its redirect host is outside sandbox network permissions; command logs remain available in the linked GitHub run. The initial manual-dispatch 403 did not block the delayed push-triggered run; see `e3_log_access_note.txt`.

## 8. Deltas, limitations and reviewer handoff

Updated `docs/deviations.md` (DEV-07..11), `docs/assumptions.md`, `docs/not_implemented.md`, `docs/USER_GUIDE.md`, requirement traceability and two-OS CI. EconML 0.17.0 / transitive sparse 0.19.3 are hash-locked and licensed in third-party notices.

Open boundaries (not hidden as PASS claims):

1. **DoWhy optional environment:** its 0.14 SciPy <=1.15.3 requirement conflicts with the locked SciPy 1.17.1. The real adapter raises `CausalBackendUnavailable` when absent; installed EconML is exercised instead. DoWhy execution is not proven.
2. **Physical D2 evidence:** no normative Win11 23H2 reference-hardware timing or human screenshot approval in this sandbox. Real Windows/Ubuntu Qt passes E3; local GUI uses headless ABI stubs.
3. **UX:** strict advanced monitoring is runnable through Python and CLI. Legacy desktop snapshots gained binding/integrity/error presentation, but no new full advanced UQ/drift control suite is claimed.
4. **Operational assumptions:** JSONL history is single-user/single-writer, unsigned (hash-chain integrity is not an adversarial signature). Calibration reference and adjustment policy must remain fixed per history. Old unbound SQLite snapshots require re-validation. Near-zero ratios/FSU are undefined explicitly.
5. **Statistical scope:** empirical synthetic coverage/backtests do not guarantee coverage after arbitrary shifts. Causal diagnostics cannot prove identification; report labels/assumptions remain conservative. Engineering review is required before any baseline replacement.
6. **Later phases:** deterministic PDF/project ZIP packaging and production Authenticode signing remain outside this phase.

Ready for the independent reviewer to fetch this branch read-only, execute the canonical G7 block and vote. Main-branch merging is reserved for the owner/reviewer workflow.
