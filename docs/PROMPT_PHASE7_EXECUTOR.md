# EnergyBaseline v6 — Phase 7 Executor Prompt: UQ + Drift + Prediction & Monitoring + Causal M&V (Gate G7)

You are the **executor**, not the judge. An independent reviewer reads your code, re-runs your commands
personally, and votes PASS / PASS-WITH-CONDITIONS / FAIL. You own only `arena/3611a52f-energybaseline`.

## 1. Phase ID and scope

**Phase ID:** `Phase 7 — UQ + Drift + Prediction & Monitoring + Causal M&V`
**Depends on:** Phase 6 (Gate G6 PASS, merged into `arena/af928d3c-energybaseline` → `arena/2124dfe7-energybaseline` @ `4f0b8d8`)
**Branch:** `arena/3611a52f-energybaseline` (sole push target; `main` and `arena/af928d3c-energybaseline` untouched;
linear history, zero force-push, no executor PR into `main`).
**Estimate:** 6–8 engineer-weeks.

Scope per `docs/EXECUTION_PLAN.md` Phase 7: §19, §20, §21, T14, AC-14..16, AC-29, AC-30.
Per `docs/requirements_matrix.md`: UQ-1..5, DR-1..5, PM-1..7, DOM-3, AC-16, AC-32 are `planned` for Phase 7.

## 2. Branch protocol (non-negotiable)

- Work only on `arena/3611a52f-energybaseline`; never the reviewer's branch, never `main`.
- No force-push.
- No executor PR into `main`.
- The reviewer fetches your branch read-only, runs the gate block **personally**, and votes.
- Docs English-only (G-7); Persian only for chat with the owner.

## 3. Deliverables (ID → file/function → test)

| Requirement / Work ID | File & Function / Class | Verification Test(s) / Gate(s) |
|---|---|---|
| UQ-1 | `energybaseline/uq/conformal.py` — distribution-free conformal intervals via MAPIE 1.6 (`SplitConformalRegressor`, `CrossConformalRegressor`, `JackknifeAfterBootstrapRegressor`, `ConformalizedQuantileRegressor`, `TimeSeriesRegressor(method="enbpi"|"aci")`) | conformal coverage test; MAPIE backend test |
| UQ-2 | `energybaseline/uq/parametric.py` — parametric OLS/HAC intervals and quantile-regression intervals cross-checked against conformal bounds | parametric-vs-conformal test |
| UQ-3 | `energybaseline/uq/calibration.py` — empirical holdout coverage calibration diagnostics verifying 80%/95% nominal intervals within ±5 pp | coverage-calibration test |
| UQ-4 | `energybaseline/uq/savings_uncertainty.py` — cumulative energy savings uncertainty propagation (serial correlation, ASHRAE/IPMVP) | savings-uncertainty test |
| UQ-5 | `energybaseline/uq/fsu.py` — Fractional Savings Uncertainty at user-selected confidence (default 90/95%) | FSU test |
| AC-16 | `energybaseline/uq/__init__.py::run_uq_catalog` — UQ calibrates 80%/95% prediction intervals via MAPIE EnbPI/ACI within ±5 pp coverage | UQ catalog test |
| DR-1 | `energybaseline/monitoring/drift.py` — covariate distribution drift: PSI + two-sample KS/Wasserstein | PSI/KS drift test |
| DR-2 | `energybaseline/monitoring/drift.py` — residual mean/variance regime-shift: rolling Page–Hinkley / Pettitt / CUSUM | regime-shift test |
| DR-3 | `energybaseline/monitoring/drift.py` — conformal interval miscoverage monitor (rolling empirical coverage degradation) | miscoverage monitor test |
| DR-4 | `energybaseline/monitoring/drift.py` — distinction: genuine efficiency improvement (sustained negative consumption residual inside valid driver domain) vs model breakdown | improvement-vs-breakdown test |
| DR-5 | `energybaseline/monitoring/drift.py` — ISO 50006 re-baselining advisory summarizing triggered drift indicators | re-baselining advisory test |
| PM-1 | `energybaseline/monitoring/prediction.py` — reporting-period ingestion validated against baseline schema, units, granularity | schema-validation test |
| PM-2 | `energybaseline/monitoring/prediction.py` — adjusted baseline `E_adj(X_reporting)` + routine/non-routine static factor adjustments | adjusted-baseline test |
| PM-3 | `energybaseline/monitoring/prediction.py` — period/cumulative EnPI ratio, absolute savings, propagated confidence bands | EnPI/savings test |
| PM-4 | `energybaseline/monitoring/prediction.py` — tabular CUSUM of energy residuals with V-mask/decision-interval alarm | CUSUM test |
| PM-5 | `energybaseline/monitoring/prediction.py` — EWMA control chart of standardized residuals | EWMA test |
| PM-6 | `energybaseline/monitoring/prediction.py` — driver validity-domain check flagging extrapolation beyond training range | domain-check test |
| PM-7 | `energybaseline/monitoring/prediction.py` — incremental monitoring batch append with audit history per reporting period | batch-append test |
| T14 (causal M&V) | `energybaseline/causal/` — T14 balance, parallel trends, propensity overlap, kernel balancing, DoWhy/EconML effect with refutation; reported as “possible savings / non-routine event” | causal M&V test; refutation test |
| AC-32 | `energybaseline/monitoring/drift.py` — drift detector triggers re-baselining advisories on covariate shift (PSI/KS) and residual regime shift | drift advisory test |
| DOM-3 | `energybaseline/monitoring/adjustments.py` — separation of routine (weather/production model) and documented non-routine (static factor) adjustments | adjustments-separation test |
| Lock snapshot | `energybaseline/lock/baseline_lock.py` — monitoring snapshot tied to baseline version + input hash | snapshot-tied-to-baseline test |

## 4. Gate G7 command block (canonical — run every command; every command must exit 0)

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
python -m pytest -q --cov=energybaseline/core --cov=energybaseline/models --cov=energybaseline/scoring --cov=energybaseline/validation --cov=energybaseline/uq --cov=energybaseline/monitoring --cov=energybaseline/causal --cov-fail-under=60
python -m pytest -q tests/gui -p no:cacheprovider
test -z "$(git ls-files | grep -Ei 'package.json|tsconfig|\.tsx?$|next\.config|drizzle|eslint|postcss')"
test -z "$(git ls-files | grep -E '__pycache__|\.pyc$')"
test -z "$(grep -rn 'weasyprint' energybaseline scripts requirements.txt requirements.in requirements.lock)"
test -z "$(grep -rn 'os.environ\\[\\\"DATABASE_URL\\\"\\]' energybaseline/launcher.py)"
test -z "$(grep -rn 'pickle.load' energybaseline/)"
test -z "$(grep -rn 'from flask\\|from fastapi\\|import uvicorn' energybaseline/)"
test -z "$(grep -rniE 'password|secret|api_key|credential' energybaseline/launcher.py energybaseline/config/ energybaseline/db/ --exclude-dir=__pycache__)"
test -z "$(grep -rniE 'drift.*retrain|retrain.*drift|auto.?retrain' energybaseline/monitoring/)"
python -m alembic upgrade head
python -m alembic downgrade base
python -m alembic upgrade head
```

## 5. Gate G7 specific rejection criteria (executor must satisfy these)

1. **Empirical coverage ≈ nominal within tolerance** — conformal intervals must achieve empirical 80%/95% holdout coverage within ±5 pp (UQ-3).
2. **Kupiec/Christoffersen on synthetic series** — coverage backtests must pass on synthetic drift/injection series.
3. **“No credential in `.ebproj`/logs/report”** — grep + ZIP inspection must confirm zero plaintext credentials.
4. **Fuzz corpus 100 % accept/reject** — malformed reporting-period inputs must be deterministically accepted or rejected (never crash).
5. **Snapshot tied to baseline version** — every monitoring snapshot must reference the locked baseline version + input hash.
6. **PSI/KS on shifted data** — drift detectors must fire on shifted distributions and stay quiet on stable data.
7. **Drift never mutates a model** — drift detection is advisory/re-validation recommendation only; never auto-retrain.

**Rejected if:**
- “distribution-free guarantee” is claimed for autocorrelated series;
- SQL validation is still regex (must use `sqlglot.parse`);
- passwords persist to disk;
- drift is wired to retraining.

**Auto-fail triggers:**
- Monitoring snapshot not tied to baseline version + input hash;
- Drift detector auto-retrains or mutates a model;
- Plaintext credentials in `.ebproj`/logs/reports;
- Editing or deleting an existing test to go green = instant FAIL;
- Running inferential tests before VSG/TCP gate passes.

## 6. Evidence requirements (E1/E2/E3 per DECISIONS.md D2 = B+A)

- **E1:** raw command output in `artifacts/phase7/*.txt` + commit SHA-256
- **E2:** machine-readable JSON produced by the product itself (system.health, system.sbom, UQ calibration JSON, drift monitor JSON, monitoring/prediction JSON, causal M&V JSON, locked-baseline snapshot JSON)
- **E3:** CI on `windows-latest` + `ubuntu-latest` (D2 physical reference hardware benchmarks are non-normative in this sandbox)

Write `artifacts/phase7/REPORT.md` with: Phase ID, full commit SHA, `git diff --stat`, ID→file→function→test table,
commands executed + raw output paths, pytest summary + coverage, artifacts + SHA-256, E3 logs/timings, deltas in
deviations/assumptions/not_implemented, what was not proven and why, open risks.

## 7. What is intentionally NOT in Phase 7 scope

- Deterministic ReportLab PDF renderer / `.ebproj` ZIP packaging — **Phases 8/9**
- Physical Windows 11 23H2 reference-machine benchmarks — **Phases 7/9 per D2** (non-normative CI in sandbox)
- Authenticode production OV/EV certificate signing — deferred per D1=B (PKG-3 → [S])
- Model store SHA-256 governance / `pickle` eradication — **Phase 6 (done)**

## 8. Leftovers carried from Phase 6 (mention in your report)

- Normative D2 physical reference hardware benchmarks (`8-core · 16 GB · NVMe · Win11 23H2 · 1920×1080@100%`) and
  human visual screenshot inspection were **not** executed; Windows E3 evidence remains CI non-normative.
- Phase 6 coverage: monitor `energybaseline/scoring` and `energybaseline/validation` and `energybaseline/lock` for regression.
- D4 R reference validation: nightly R job evidence expires after 14 days without a run — ensure
  `tests_catalog/r_references/` JSON is versioned and current (< 14 days).
- Phase 6 §9 Risk 2 (conformal calibration under regime shifts/serial correlation → Phase 7 UQ-1/UQ-3).

## 9. Open risks and their effect on Phase 7 → Phase 8

1. **Conformal interval calibration under regime shifts & serial correlation (UQ-1/UQ-3):** standard exchangeable
   split conformal intervals under-cover when residuals are serially correlated/heteroskedastic; Phase 7 must pair
   F1–F10 point predictions with sequential EnbPI/ACI conformal calibration and verify empirical 80%/95% holdout coverage.
2. **Drift detector false positives under seasonal loading (DR-1..3):** PSI/KS on raw drivers can fire on legitimate
   seasonal swings; Phase 7 must condition drift indicators on driver validity domain (PM-6) and residual regime
   (DR-2) before raising re-baselining advisories.
3. **14-day R reference rolling freshness (TC-8 / CI-3):** committed R reference JSONs must be refreshed by
   `.github/workflows/r-reference.yml` before staleness hard-fails `tests/test_reference_staleness.py`.

## 10. What the reviewer rejects (auto-fail triggers)

- Test claims without raw output; mock-centric tests standing in for `[M]` IDs; test tampering.
- Hardcoded metrics in report templates; Windows claims without E3; adding a web stack.
- Silent data mutation; dynamic `eval`/`getattr` in the dispatcher; loading pickle without SHA-256.
- Editing or deleting an existing test to go green = instant FAIL.
- Running inferential tests before VSG/TCP gate passes.
- Drift detector auto-retrains/mutates a model; surrogate presented as the model's formula.
- Plaintext credentials in `.ebproj`/logs/reports; monitoring snapshot not tied to baseline version.
- “Distribution-free guarantee” claimed for autocorrelated series; SQL validation still regex.
- Stale R reference JSON (>14 days without nightly run).

## 11. Definition of Done

`pytest -q` green · `pyflakes` clean · `requirements_matrix.md` updated (no `done` row without tests;
UQ-1..5, DR-1..5, PM-1..7, AC-16, AC-32, DOM-3 advanced as appropriate) · `deviations.md` updated · a runnable
artifact (not a mock) · no regression in earlier phase tests · every `[M]` ID has an automated test or documented
manual check **with an evidence file** · empirical coverage ≈ nominal within ±5 pp · Kupiec/Christoffersen pass ·
no credential in `.ebproj`/logs/report · fuzz corpus 100 % accept/reject · snapshot tied to baseline version ·
PSI/KS fire on shifted data, quiet on stable · drift never mutates a model · R reference JSON current (<14 days) ·
Gate G7 criteria all satisfied.

---

Language compliance (G-7): 100% English across all code, docstrings, comments, PowerShell scripts, tests,
workflows, commit messages, and reports.

Licence: Proprietary / Internal (`LICENSE` committed; do not modify). `artifacts/review_baseline/` must not be
modified or deleted.
