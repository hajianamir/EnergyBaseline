# EnergyBaseline v6 — Phase 4 Executor Prompt: Test Catalog T1–T14

You are the **executor**, not the judge. An independent reviewer reads your code, re-runs your commands
personally, and votes PASS / PASS-WITH-CONDITIONS / FAIL. You own only `arena/3611a52f-energybaseline`.

## 1. Phase ID and scope

**Phase ID:** `Phase 4 — Test Catalog T1–T14`
**Depends on:** Phase 3 (Gate G3 PASS, merged into `arena/af928d3c-energybaseline` @ `80a0deb`)
**Branch:** `arena/3611a52f-energybaseline` (sole push target; `main` and `arena/af928d3c-energybaseline` untouched;
linear history, zero force-push, no executor PR into `main`).
**Estimate:** 6–8 engineer-weeks.

Scope per `docs/EXECUTION_PLAN.md` §5 (Phase 4): §14 (T1..T14, TC-1..8), §15 (CI-1..4), §27 (R references),
AC-09, AC-25, AC-26.

## 2. Branch protocol (non-negotiable)

- Work only on `arena/3611a52f-energybaseline`; never the reviewer's branch, never `main`.
- No force-push.
- No executor PR into `main`.
- The reviewer fetches your branch read-only, runs the gate block **personally**, and votes.
- Docs English-only (G-7); Persian only for chat with the owner.

## 3. Deliverables (ID → file/function → test)

| Requirement / Work ID | File & Function / Class | Verification Test(s) / Gate(s) |
|---|---|---|
| T1 | `energybaseline/tests_catalog/t1_stationarity.py` — Augmented Dickey-Fuller / KPSS stationarity test | contract: statistic, p_raw, p_adj, df, effect_size, ci, n_used, assumptions_checked, decision, interpretation, downstream_action, mode |
| T2 | `energybaseline/tests_catalog/t2_seasonality.py` — Seasonality detection (ACF/PACF peaks, CH test) | same contract |
| T3 | `energybaseline/tests_catalog/t3_autocorrelation.py` — Ljung-Box / Durbin-Watson residual autocorrelation | same contract |
| T4 | `energybaseline/tests_catalog/t4_normality.py` — Shapiro-Wilk / Jarque-Bera residual normality | same contract |
| T5 | `energybaseline/tests_catalog/t5_heteroskedasticity.py` — Breusch-Pagan / White heteroskedasticity | same contract |
| T6 | `energybaseline/tests_catalog/t6_multicollinearity.py` — VIF / condition index multicollinearity | same contract |
| T7 | `energybaseline/tests_catalog/t7_structural_break_chow.py` — Chow structural break test (R reference) | same contract + R reference JSON |
| T8 | `energybaseline/tests_catalog/t8_structural_break_quandt_andrews.py` — Quandt–Andrews break test (bootstrap p) | same contract + R reference JSON |
| T9 | `energybaseline/tests_catalog/t9_pettitt.py` — Pettitt change-point test (R reference) | same contract + R reference JSON |
| T10 | `energybaseline/tests_catalog/t10_diebold_mariano.py` — Diebold–Mariano / HLN forecast comparison (OOF) | same contract + R reference JSON |
| T11 | `energybaseline/tests_catalog/t11_nemenyi.py` — Nemenyi post-hoc comparison (OOF) | same contract + R reference JSON |
| T12 | `energybaseline/tests_catalog/t12_little_mcar.py` — Little's MCAR missingness test (native) | same contract + R reference JSON |
| T13 | `energybaseline/tests_catalog/t13_outlier_influence.py` — flag-only influence / Cook's distance | same contract |
| T14 | `energybaseline/tests_catalog/t14_causal_group.py` — causal M&V with `group` column (balance, parallel trends, propensity overlap) | same contract; only with `group` column |
| TC-1..TC-8 | `energybaseline/tests_catalog/tc_assumptions.py` — declared assumptions with logged robust/non-parametric fallback; HAC/block bootstrap with "naive" labelling; stationarity decision matrix; bootstrap p for Quandt–Andrews; native Little's MCAR; flag-only influence; documented subsampling for large n | assumption-log tests per TC-ID |
| R references | `tests_catalog/r_references/*.json` — versioned R reference fixtures (Chow, Quandt–Andrews, Pettitt, Diebold–Mariano/HLN, Nemenyi, Little's MCAR, MCS) | stale-R-reference detection gate |
| Type I error / power | `energybaseline/tests_catalog/monte_carlo.py` — Type I error Monte Carlo ≈ α; power against planted effects | MC calibration test |
| CI method | `energybaseline/tests_catalog/ci_method.py` — every key estimate carries a CI at selected confidence level with stated method | CI-per-estimate test |

## 4. Gate G4 command block (canonical — run every command; every command must exit 0)

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
python scripts/gen_matrix.py --check
python -m pytest -q --cov=energybaseline/core --cov-fail-under=60
python -m pytest -q tests/gui -p no:cacheprovider
test -z "$(git ls-files | grep -Ei 'package.json|tsconfig|\.tsx?$|next\.config|drizzle|eslint|postcss')"
test -z "$(git ls-files | grep -E '__pycache__|\.pyc$')"
test -z "$(grep -rn 'weasyprint' energybaseline scripts requirements.txt requirements.in requirements.lock)"
test -z "$(grep -rn 'os.environ\[\"DATABASE_URL\"\]' energybaseline/launcher.py)"
python -m alembic upgrade head
python -m alembic downgrade base
python -m alembic upgrade head
```

## 5. Gate G4 specific rejection criteria (executor must satisfy these)

1. **At least one passing and one failing case per T-ID** — every T1..T14 must demonstrate both a pass and a fail path.
2. **`"not run (user)"` appears in the report** — tests not executed by the user must be explicitly labelled, never silently skipped as passing.
3. **No "observed power"** — post-hoc observed power must not be reported as a decision input.
4. **`"naive"` labelling on autocorrelated series** — autocorrelated inputs without HAC/block bootstrap must be labelled `naive`.
5. **Stale R reference detection** — R reference JSON older than 14 days without a nightly run must fail the gate.
6. **Sub-gates G4a (T1–T4), G4b (T5–T9), G4c (T10–T14)** — each sub-gate must independently pass.

**Auto-fail triggers:**
- A custom test ships without an R reference.
- Adjusted p is reported where raw is meant (or vice versa).
- `p_and_effect_size` is claimed while effect size is ignored in the decision.
- Editing or deleting an existing test to go green = instant FAIL.

## 6. Evidence requirements (E1/E2/E3 per DECISIONS.md D2 = B+A)

- **E1:** raw command output in `artifacts/phase4/*.txt` + commit SHA-256
- **E2:** machine-readable JSON produced by the product itself (`system.health`, `system.sbom`, R reference JSON, test contract JSON)
- **E3:** CI on `windows-latest` + `ubuntu-latest` (D2 physical reference hardware benchmarks are non-normative in this sandbox)

Write `artifacts/phase4/REPORT.md` with: Phase ID, full commit SHA, `git diff --stat`, ID→file→function→test table,
commands executed + raw output paths, pytest summary + coverage, artifacts + SHA-256, E3 logs/timings, deltas in
deviations/assumptions/not_implemented, what was not proven and why, open risks.

## 7. What is intentionally NOT in Phase 4 scope

- Model store SHA-256 governance / `pickle` eradication in `store.py` (Phase 6)
- SQL connector DPAPI / secret-lifetime hardening (Phase 7)
- ReportLab PDF renderer / `.ebproj` ZIP packaging (Phases 8/9)
- Physical Windows 11 23H2 reference-machine latency benchmarks (Phase 7/9 per D2)
- Authenticode production OV/EV certificate signing (deferred per D1=B; PKG-3 → [S])

## 8. Leftovers carried from Phase 3 (mention in your report)

- Normative D2 physical reference hardware benchmarks (`8-core · 16 GB · NVMe · Win11 23H2 · 1920×1080@100%`) and
  human visual screenshot inspection were **not** executed; Windows E3 evidence remains CI non-normative.
- Phase 3 coverage: monitor `energybaseline/core/cache.py` (86%) and `energybaseline/core/integrity.py` (89%) for regression.
- D4 R reference validation: nightly R job evidence expires after 14 days without a run — ensure `tests_catalog/r_references/` JSON is versioned and current.

## 9. Open risks and effect on Phase 4 (from Phase 3 §9)

1. **Phase 4 Catalog Plug-In Contract (`cache.py` → `tests_catalog`):** Phase 4 statistical tests (T1–T14, including
   structural breaks, cointegration, seasonal unit roots, and subgroup invariance) must emit
   `RawTestArtifact`-compatible raw p-values, test statistics, effect sizes, and confidence bounds so
   `ResultCache` and zero-recomputation `what_if` remain intact without recomputing test statistics on
   α/correction edits.
2. **Small-sample / degenerate series edge cases in T1–T14:** Hypothesis fuzzing in Phase 3 surfaced
   constant/high-missing/short-series inputs cleanly; Phase 4 tests must preserve explicit assumption checks
   and non-parametric fallbacks (G-5) without raising unhandled numerical exceptions.
3. **High-DPI vertical budget on `SetupPage`:** Adding Phase 4 per-test diagnostic visualizations must continue
   using scrollable containers or secondary dialogs so 175% High-DPI window height (<= 411 px logical frame
   height) remains within bounds.

## 10. What the reviewer rejects (auto-fail triggers)

- Test claims without raw output; mock-centric tests standing in for `[M]` IDs; test tampering.
- Hardcoded metrics in report templates; Windows claims without E3; adding a web stack.
- Silent data mutation; dynamic `eval`/`getattr` in the dispatcher; loading pickle without SHA-256.
- Editing or deleting an existing test to go green = instant FAIL.
- Running inferential tests before VSG/TCP gate passes.
- Adjusted p-value not stored; what-if recomputing instead of 0 engine calls.
- `post_hoc` appearing outside the UI layer.
- A custom test without an R reference; adjusted/raw p confusion; effect size claimed but ignored in decision.
- Stale R reference JSON (>14 days without nightly run).

## 11. Definition of Done

`pytest -q` green · `pyflakes` clean · `requirements_matrix.md` updated (no `done` row without tests) ·
`deviations.md` updated · a runnable artifact (not a mock) · no regression in earlier phase tests · every `[M]` ID
has an automated test or documented manual check **with an evidence file** · at least one passing and one failing
case per T-ID · `"not run (user)"` labelled in report · no observed power · `"naive"` labelling on autocorrelated
series · R reference JSON current (<14 days) · sub-gates G4a/G4b/G4c each pass independently.

---

Language compliance (G-7): 100% English across all code, docstrings, comments, PowerShell scripts, tests, workflows,
commit messages, and reports.

Licence: Proprietary / Internal (`LICENSE` committed; do not modify). `artifacts/review_baseline/` must not be
modified or deleted.