# EnergyBaseline v6 — Phase 6 Executor Prompt: Modelling / Validation / Scoring / Top-5 / Lock (Gate G6)

You are the **executor**, not the judge. An independent reviewer reads your code, re-runs your commands
personally, and votes PASS / PASS-WITH-CONDITIONS / FAIL. You own only `arena/3611a52f-energybaseline`.

## 1. Phase ID and scope

**Phase ID:** `Phase 6 — Modelling / Validation / Scoring / Top-5 / Lock`
**Depends on:** Phase 5 (Gate G5 PASS, merged into `arena/af928d3c-energybaseline` @ `c73a6a3` → now `arena/2124dfe7-energybaseline` @ `12ce612`)
**Branch:** `arena/3611a52f-energybaseline` (sole push target; `main` and `arena/af928d3c-energybaseline` untouched;
linear history, zero force-push, no executor PR into `main`).
**Estimate:** 6–8 engineer-weeks.

Scope per `docs/EXECUTION_PLAN.md` Phase 6: §17, §18, MT-1..5, VAL-1..7, SC-1..10, AC-07, AC-10..13, AC-31, AC-32.
Note per `docs/requirements_matrix.md`: VAL-7 (Model Evaluation Register) is `done` from Phase 5; VAL-1..6, SC-1..10,
AC-31 remain `planned` for Phase 6; AC-07 (VSG pre-modeling) is `done` from Phase 3 and must be preserved.

## 2. Branch protocol (non-negotiable)

- Work only on `arena/3611a52f-energybaseline`; never the reviewer's branch, never `main`.
- No force-push.
- No executor PR into `main`.
- The reviewer fetches your branch read-only, runs the gate block **personally**, and votes.
- Docs English-only (G-7); Persian only for chat with the owner.

## 3. Deliverables (ID → file/function → test)

| Requirement / Work ID | File & Function / Class | Verification Test(s) / Gate(s) |
|---|---|---|
| [M] portfolio | `energybaseline/models/` (F1–F10 from Phase 5) + new wrappers — mean, degree-day, MLR, Ridge, Lasso, EN, piecewise 1P–5P, TOWT, RF, ExtraTrees, GB, XGBoost, LightGBM, SVR-RBF, MLP, one sequence NN; `[S]`/`[O]` tracked per family | model-catalog CLI test; [S]/[O] status register test |
| VAL-1 | `energybaseline/validation/splitting.py` — chronological train/validation/holdout split; random k-fold shuffling forbidden on time series | chronological split test; anti-shuffle test |
| VAL-2 | `energybaseline/validation/walkforward.py` — expanding/rolling walk-forward CV, configurable horizon, embargo/gap = max lag | walk-forward CV test; embargo/gap sizing test |
| VAL-3 | `energybaseline/validation/nested_hpo.py` — nested HPO (Optuna bounded trials) strictly inside inner walk-forward folds | nested HPO in-fold test; no outer-fold leak test |
| VAL-4 | `energybaseline/validation/preprocessing.py` — pipeline-encapsulated preprocessing (imputation, scaling, DD knots, target lags) fitted exclusively per fold | per-fold fit test; no cross-fold fit test |
| VAL-5 | `energybaseline/validation/leakage_canary.py` — automated data-leakage canary: future target perturbations never alter historical fold predictions | leakage canary accept/reject test |
| VAL-6 | `energybaseline/validation/ashrae14_metrics.py` — ASHRAE Guideline 14 OOS metrics: CV(RMSE), NMBE, R2, MAE, MAPE, sMAPE, WAPE | ASHRAE 14 metrics test |
| VAL-7 | `energybaseline/models/base.py::ModelEvaluationEntry` + `energybaseline/models/__init__.py::evaluate_f1_f10_catalog` (done Phase 5) — preserve, no regression | register completeness test (re-run) |
| Purge gap | `energybaseline/validation/purge.py` — purge gap = **max(1 day, 1 step, max lag, max rolling window)** (not fixed 1 day) | purge-gap-grows-with-lag test |
| In-fold preprocessing/selection | `energybaseline/pipeline.py` — imputer/scaler/selector fit inside folds only | in-fold fit test |
| NN gate | `energybaseline/models/base.py` — NN gate both ways with logged reason; `OptionalBackendSkippedError` / `SampleSizeGateSkippedError` preserved | NN gate accept/skip test; logged-reason test |
| Ensembles / NNLS | `energybaseline/models/ensemble.py` — ensembles only with >3 % gain; **NNLS on inner-fold OOF** evaluated on outer folds; `k_max=min(p,n/20)` | NNLS inner-fold-only test; >3% gain threshold test; k_max test |
| 13-criteria scoring | `energybaseline/scoring/composite.py` — 13-criteria scoring with weights, ±20 % sensitivity, deterministic tie-breaks | scoring determinism test; sensitivity test; tie-break test |
| Registers | `energybaseline/scoring/registers.py` — separate Rejected / Qualified-not-shortlisted registers; SC-9 handling; explicit user acknowledgement | register separation test; acknowledgement test |
| SC-4 | `energybaseline/scoring/tiebreak.py` — parsimony/interpretability tie-breaking favoring simpler physical/linear baselines within MCS (T13) | SC-4 rank stability test |
| SC-7 | `energybaseline/scoring/override.py` — manual engineer baseline override with documented justification in audit log | override + audit-log test |
| SC-9 | `energybaseline/scoring/envelope.py` — baseline validity domain envelope (min/max/convex-hull quantiles of training drivers) for extrapolation warnings | envelope extrapolation warning test |
| Top-5 | `energybaseline/models/top5.py` + `energybaseline/gui/pages_results.py` — Top-5 visible before lock; SC-7 representations (closed-form equations for parametric; architecture + hyperparameters + labelled surrogate with fidelity R² for non-parametric) | Top-5-before-lock test; surrogate-fidelity-R² test |
| Lock | `energybaseline/lock/baseline_lock.py` — immutable versioned baseline: artifact SHA-256, features, pipeline, VSG/TCP snapshots, window, thresholds, seed, calibration summary, model card; lock immutability + audit trail | lock immutability test; audit trail test; Excel-formula round-trip numerics test |
| AC-10..13 | `energybaseline/core/pipeline.py`, `energybaseline/models/` — TCP acceptance, gated evaluation, model registry status | VSG/TCP gate-before-fit tests |

## 4. Gate G6 command block (canonical — run every command; every command must exit 0)

Every command below must exit 0. The `test -z "$(…)"` lines assert empty output on purpose: a bare `grep`
exits 1 on a clean tree, which would contradict "must exit 0".

```bash
python -m pytest -q
python -m pytest -q tests/gates
python -m ruff check . && python -m ruff format --check .
python -m pyflakes energybaseline
python scripts/gates/guard_web_free.py
python scripts/gates/guard_env_readers.py
python scripts/gates/guard_tests_integrity.py
python scripts/gates/guard_no_qthread_compute.py
python scripts/gen_matrix.py --check
python -m pytest -q --cov=energybaseline/core --cov=energybaseline/models --cov=energybaseline/scoring --cov=energybaseline/validation --cov-fail-under=60
python -m pytest -q tests/gui -p no:cacheprovider
test -z "$(git ls-files | grep -Ei 'package.json|tsconfig|\.tsx?$|next\.config|drizzle|eslint|postcss')"
test -z "$(git ls-files | grep -E '__pycache__|\.pyc$')"
test -z "$(grep -rn 'weasyprint' energybaseline scripts requirements.txt requirements.in requirements.lock)"
test -z "$(grep -rn 'os.environ\\[\\\"DATABASE_URL\\\"\\]' energybaseline/launcher.py)"
test -z "$(grep -rn 'pickle.load' energybaseline/)"
test -z "$(grep -rn 'from flask\\|from fastapi\\|import uvicorn' energybaseline/)"
python -m alembic upgrade head
python -m alembic downgrade base
python -m alembic upgrade head
python scripts/benchmarks/bench_1m.py
```

## 5. Gate G6 specific rejection criteria (executor must satisfy these)

1. **Purge gap grows with a larger lag** — the purge gap is `max(1 day, 1 step, max lag, max rolling window)`, not a fixed 1 day.
2. **Scaler/imputer/selector fit inside folds** — no preprocessing fitted on the full frame before fold split.
3. **`k_max=min(p,n/20)`** — enforced for feature selection / regularization paths.
4. **NN gate both ways** — NN accepted or skipped with a logged reason, in both directions.
5. **NNLS weights fitted on inner folds only** — evaluated on outer folds; no outer-fold leakage.
6. **Deterministic tie-break order** — identical inputs produce identical Top-5 / register ordering.
7. **SC-4 rank stability** — parsimony/interpretability tie-breaking favoring simpler baselines within MCS.
8. **Lock immutability and audit trail** — locked baseline cannot be mutated; every lock recorded in audit log with artifact SHA-256.
9. **Excel-formula round-trip numerics** — exported numeric values round-trip through Excel formulas without drift.

**Rejected if:**
- Top-5 is not visible before lock, or lock is mutable;
- rejected models are mixed with "merely lower score" models;
- the purge gap stays at one day (must grow with lag);
- the surrogate is presented as the model's formula (must be labelled surrogate with fidelity R²).

**Auto-fail triggers:**
- A model family ships without a planted-effect recovery test (carried from Phase 5).
- Regime/cluster labels leak into model inputs (`RegimeLabelLeakageError`).
- Editing or deleting an existing test to go green = instant FAIL.
- Running inferential tests before VSG/TCP gate passes.
- Rejected models commingled with Qualified-not-shortlisted.

## 6. Evidence requirements (E1/E2/E3 per DECISIONS.md D2 = B+A)

- **E1:** raw command output in `artifacts/phase6/*.txt` + commit SHA-256
- **E2:** machine-readable JSON produced by the product itself (system.health, system.sbom, model evaluation register JSON, lock baseline JSON, scoring register JSON)
- **E3:** CI on `windows-latest` + `ubuntu-latest` (D2 physical reference hardware benchmarks are non-normative in this sandbox)

Write `artifacts/phase6/REPORT.md` with: Phase ID, full commit SHA, `git diff --stat`, ID→file→function→test table,
commands executed + raw output paths, pytest summary + coverage, artifacts + SHA-256, E3 logs/timings, deltas in
deviations/assumptions/not_implemented, what was not proven and why, open risks.

## 7. What is intentionally NOT in Phase 6 scope

- Conformal UQ (`UQ-1..5`), MAPIE conformal calibration, EnbPI/ACI — **Phase 7 (`Gate G7`)**
- Drift detection (PSI/KS/Wasserstein/CUSUM/EWMA/Page–Hinkley), drift monitoring (`DR-1..5`, `PM-1..7`) — **Phase 7**
- SQL connector DPAPI / secret-lifetime hardening, credentials in keyring/DPAPI — **Phase 7**
- Multi-target / portfolio rollup (`MT-1..5` per matrix phase 7; EXECUTION_PLAN lists MT-1..5 in Phase 6 — implement as scoped) — partially Phase 6/7
- Deterministic ReportLab PDF renderer / `.ebproj` ZIP packaging — **Phases 8/9**
- Physical Windows 11 23H2 reference-machine benchmarks — **Phases 7/9 per D2**
- Authenticode production OV/EV certificate signing — deferred per D1=B (PKG-3 → [S])

## 8. Leftovers carried from Phase 5 (mention in your report)

- Normative D2 physical reference hardware benchmarks (`8-core · 16 GB · NVMe · Win11 23H2 · 1920×1080@100%`) and
  human visual screenshot inspection were **not** executed; Windows E3 evidence remains CI non-normative.
- Phase 5 coverage: monitor `energybaseline/mining + models` (92.18%) and `energybaseline/core` (83.21%) for regression.
- D4 R reference validation: nightly R job evidence expires after 14 days without a run — ensure
  `tests_catalog/r_references/` JSON is versioned and current (< 14 days; refresh before `2026-11-17` per Phase 5 §9).
- Phase 5 §9 Risk 1 (OOF alignment) and Risk 2 (autocorr-aware embargo gap) are resolved in `energybaseline/models/base.py`
  (`align_oof_predictions`, `recommend_autocorr_embargo_gap`) — preserve, no regression.

## 9. Open risks and their effect on Phase 6 → Phase 7

1. **Nested Optuna / grid budget inside outer walk-forward folds (`VAL-2`/`VAL-3`):** evaluating F5 ensembles,
   F6 LSTM, F7 SARIMAX inside nested walk-forward folds can multiply wall-clock fit time if inner trials are
   unconstrained; Phase 6 must enforce strict per-family trial/time caps and sample-size gates inside inner folds.
2. **Conformal interval calibration under regime shifts & serial correlation (`UQ-1` → Phase 7):** standard exchangeable
   split conformal intervals under-cover when residuals are serially correlated/heteroskedastic; Phase 7 must pair
   F1–F10 point predictions with sequential EnbPI/ACI conformal calibration and verify empirical 80%/95% holdout coverage.
3. **14-day R reference rolling freshness (`TC-8` / `CI-3`):** committed R reference JSONs must be refreshed by
   `.github/workflows/r-reference.yml` before staleness hard-fails `tests/test_reference_staleness.py`.

## 10. What the reviewer rejects (auto-fail triggers)

- Test claims without raw output; mock-centric tests standing in for `[M]` IDs; test tampering.
- Hardcoded metrics in report templates; Windows claims without E3; adding a web stack.
- Silent data mutation; dynamic `eval`/`getattr` in the dispatcher; loading pickle without SHA-256.
- Editing or deleting an existing test to go green = instant FAIL.
- Running inferential tests before VSG/TCP gate passes.
- Regime/cluster labels leaking into model inputs; surrogate presented as the model's formula.
- Lock mutable or Top-5 not visible before lock; rejected models mixed with Qualified-not-shortlisted.
- Purge gap fixed at one day; preprocessing fitted outside folds; NNLS weights fitted on outer folds.
- Stale R reference JSON (>14 days without nightly run).

## 11. Definition of Done

`pytest -q` green · `pyflakes` clean · `requirements_matrix.md` updated (no `done` row without tests;
VAL-1..6, SC-1..10, AC-31 advanced as appropriate; VAL-7 stays `done`) · `deviations.md` updated · a runnable
artifact (not a mock) · no regression in earlier phase tests · every `[M]` ID has an automated test or documented
manual check **with an evidence file** · purge gap grows with lag · scaler/imputer/selector fit inside folds ·
`k_max=min(p,n/20)` · NN gate both ways with logged reason · NNLS weights on inner folds only · deterministic
tie-breaks · SC-4 rank stability · lock immutability + audit trail · Top-5 visible before lock · R reference JSON
current (<14 days) · Gate G6 criteria all satisfied.

---

Language compliance (G-7): 100% English across all code, docstrings, comments, PowerShell scripts, tests,
workflows, commit messages, and reports.

Licence: Proprietary / Internal (`LICENSE` committed; do not modify). `artifacts/review_baseline/` must not be
modified or deleted.
