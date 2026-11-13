# EnergyBaseline v6 — Phase 5 Executor Prompt: EDA / Mining F1–F10 + Large-Data Performance

You are the **executor**, not the judge. An independent reviewer reads your code, re-runs your commands
personally, and votes PASS / PASS-WITH-CONDITIONS / FAIL. You own only `arena/3611a52f-energybaseline`.

## 1. Phase ID and scope

**Phase ID:** `Phase 5 — EDA / Mining F1–F10 + Large-Data Performance`
**Depends on:** Phase 4 (Gate G4 PASS, merged into `arena/af928d3c-energybaseline` @ `fc0562ab`)
**Branch:** `arena/3611a52f-energybaseline` (sole push target; `main` and `arena/af928d3c-energybaseline` untouched;
linear history, zero force-push, no executor PR into `main`).
**Estimate:** 6–8 engineer-weeks.

Scope per `docs/EXECUTION_PLAN.md` Phase 5: §16, UX-5/UX-8 (tables), IPC-7, AC-27; F1–F10 per
`docs/requirements_matrix.md`.

## 2. Branch protocol (non-negotiable)

- Work only on `arena/3611a52f-energybaseline`; never the reviewer's branch, never `main`.
- No force-push.
- No executor PR into `main`.
- The reviewer fetches your branch read-only, runs the gate block **personally**, and votes.
- Docs English-only (G-7); Persian only for chat with the owner.

## 3. Deliverables (ID → file/function → test)

| Requirement / Work ID | File & Function / Class | Verification Test(s) / Gate(s) |
|---|---|---|
| F1 | `energybaseline/models/f1_baseline.py` — OLS / WLS / HAC (Newey-West) linear energy baseline models | planted-effect recovery test; coefficient + CI contract |
| F2 | `energybaseline/models/f2_ashrae.py` — ASHRAE Guideline 14 / ISO 50006 piecewise change-point (2P, 3P-cooling, 3P-heating, 4P, 5P) + variable-base HDD/CDD | change-point detection test; HDD/CDD contract |
| F3 | `energybaseline/models/f3_regularized.py` — Ridge, Lasso, ElasticNet, HuberRegressor, Quantile Regression | regularization path test; robustness to outliers |
| F4 | `energybaseline/models/f4_basis.py` — Polynomial / spline / GAM-like basis regressions + interaction-term linear baselines | basis-expansion test; interaction contract |
| F5 | `energybaseline/models/f5_ensemble.py` — RandomForest, ExtraTrees, XGBoost, LightGBM, CatBoost with monotonic constraints | ensemble fit/predict test; monotonicity contract |
| F6 | `energybaseline/models/f6_neural.py` — scikit-learn MLPRegressor (always) + PyTorch CPU LSTM (Full profile; clean skip in Lite) gated by sample size | neural fit test; small-data skip test |
| F7 | `energybaseline/models/f7_timeseries.py` — SARIMAX / ARIMAX with exogenous drivers + ETS / Holt-Winters | seasonal fit test; exogenous driver contract |
| F8 | `energybaseline/models/f8_regime.py` — Regime-switching / operational-mode segmented baselines stratified by plant state or calendar regime; consensus via Kendall's W | regime segmentation test; Kendall's W rank-agreement test |
| F9 | `energybaseline/models/f9_decomposition.py` — Decomposition + regression hybrid (STL/Fourier + regressor); transparent formula + per-variable breakdown; optional Prophet adapter when installed | decomposition test; per-variable breakdown contract |
| F10 | `energybaseline/models/f10_stacking.py` — Constrained convex stacking / weighted ensemble of top diverse candidate families | stacking weight simplex test; out-of-fold blend test |
| Clustering / validity | `energybaseline/mining/clustering.py` — clustering with validity indices + bootstrap ARI; regime labels never model inputs (MT-5) | ARI stable-vs-unstable cluster test; label-isolation test |
| Large tables | `energybaseline/gui/pages_results.py` + table model — `QAbstractTableModel` + `fetchMore` + `QSortFilterProxyModel`; 3,000-row ceiling removed from data paths | `fetchMore` on 1M rows test; no-QTableWidget test |
| Downsampling | `energybaseline/mining/downsample.py` — LTTB / M4 downsampling for large charts | downsample error-bound test; chart-size test |
| 1M-row benchmark | `scripts/benchmarks/bench_1m.py` — 1M-row end-to-end performance benchmark | 1M-row runtime/memory test |
| F10 optional EDA attach | `energybaseline/mining/eda_attach.py` — automated EDA attach labelled "descriptive, not inferential" | descriptive-label test; no-inferential-claims test |
| UX-5 / UX-8 tables | `energybaseline/gui/pages_results.py` — Model Evaluation Register table; keyboard tab order + accessible labels | register rendering test; accessibility test |

## 4. Gate G5 command block (canonical — run every command; every command must exit 0)

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
test -z "$(grep -rn 'os.environ[\\"DATABASE_URL\\"]' energybaseline/launcher.py)"
python -m alembic upgrade head
python -m alembic downgrade base
python -m alembic upgrade head
python scripts/benchmarks/bench_1m.py
```

## 5. Gate G5 specific rejection criteria (executor must satisfy these)

1. **Planted-effect recovery within documented tolerances** — every F1–F10 family must recover a planted effect within its documented tolerance band.
2. **`fetchMore` on 1M rows** — the large table model must page through 1M rows without loading all rows into memory.
3. **ARI on stable vs unstable clusters** — clustering must show significantly higher adjusted Rand index on stable vs unstable clusterings (validity + bootstrap ARI).
4. **Proof that labels never reach the model** — regime/cluster labels must be demonstrably excluded from model inputs (MT-5).

**Rejected if:**
- clustering ships without validity/stability indices;
- large tables remain `QTableWidget`;
- method comparison lacks rank agreement (Kendall's W for F8).

**Auto-fail triggers:**
- A model family ships without a planted-effect recovery test.
- Regime/cluster labels leak into model inputs.
- `fetchMore` materializes the full 1M-row frame.
- Editing or deleting an existing test to go green = instant FAIL.
- Running inferential claims before the VSG/TCP gate passes.

## 6. Evidence requirements (E1/E2/E3 per DECISIONS.md D2 = B+A)

- **E1:** raw command output in `artifacts/phase5/*.txt` + commit SHA-256
- **E2:** machine-readable JSON produced by the product itself (system.health, system.sbom, benchmark JSON, clustering validity JSON)
- **E3:** CI on `windows-latest` + `ubuntu-latest` (D2 physical reference hardware benchmarks are non-normative in this sandbox)

Write `artifacts/phase5/REPORT.md` with: Phase ID, full commit SHA, `git diff --stat`, ID→file→function→test table,
commands executed + raw output paths, pytest summary + coverage, artifacts + SHA-256, E3 logs/timings, deltas in
deviations/assumptions/not_implemented, what was not proven and why, open risks.

## 7. What is intentionally NOT in Phase 5 scope

- Full model zoo OOF cross-validation integration (VAL-1..7, SC-1..10) — scheduled for **Phase 6**
- SQL connector DPAPI / secret-lifetime hardening — **Phase 7**
- ReportLab PDF renderer / `.ebproj` ZIP packaging — **Phases 8/9**
- Physical Windows 11 23H2 reference-machine latency benchmarks — **Phase 7/9 per D2**
- Authenticode production OV/EV certificate signing — deferred per D1=B (PKG-3 → [S])

## 8. Leftovers carried from Phase 4 (mention in your report)

- Normative D2 physical reference hardware benchmarks (`8-core · 16 GB · NVMe · Win11 23H2 · 1920×1080@100%`) and
  human visual screenshot inspection were **not** executed; Windows E3 evidence remains CI non-normative.
- Phase 4 coverage: monitor `energybaseline/core/cache.py` (87%) and `energybaseline/core/integrity.py` (89%) for regression.
- D4 R reference validation: nightly R job evidence expires after 14 days without a run — ensure
  `tests_catalog/r_references/` JSON is versioned and current (< 14 days).

## 9. Open risks and effect on Phase 5 (from Phase 4 §9)

1. **OOF alignment across heterogeneous model horizons (T10/T11 → Phase 5/6):** multi-step forecasting and
   lagged-feature models (F6 LSTM, F7 SARIMAX) can trim leading warm-up rows; candidate predictions must be
   aligned on an identical common OOF timestamp index before Diebold–Mariano / MCS and Friedman + Nemenyi.
2. **Autocorrelation-aware downstream fold sizing (T1/T3 → Phase 5/6):** when T1 or T3 flags high lag-1
   persistence, blocked/rolling temporal cross-validation should honor embargo/gap lengths scaled to the
   empirical autocorrelation decay to prevent fold-boundary leakage.
3. **14-day R reference rolling freshness (TC-8 / CI-3):** `tests/test_reference_staleness.py` hard-fails when
   committed R reference JSON timestamps exceed 14 days without a refresh — the nightly
   `.github/workflows/r-reference.yml` workflow (CI-3) must remain active.

## 10. What the reviewer rejects (auto-fail triggers)

- Test claims without raw output; mock-centric tests standing in for `[M]` IDs; test tampering.
- Hardcoded metrics in report templates; Windows claims without E3; adding a web stack.
- Silent data mutation; dynamic `eval`/`getattr` in the dispatcher; loading pickle without SHA-256.
- Editing or deleting an existing test to go green = instant FAIL.
- Running inferential tests before VSG/TCP gate passes.
- Regime/cluster labels leaking into model inputs; `fetchMore` materializing full frames.
- `post_hoc` appearing outside the UI layer.
- A model family without a planted-effect recovery test; clustering without validity/stability.
- Large tables remaining `QTableWidget`; method comparison without rank agreement.
- Stale R reference JSON (>14 days without nightly run).

## 11. Definition of Done

`pytest -q` green · `pyflakes` clean · `requirements_matrix.md` updated (no `done` row without tests;
F1–F10 advanced as appropriate) · `deviations.md` updated · a runnable artifact (not a mock) · no regression in
earlier phase tests · every `[M]` ID has an automated test or documented manual check **with an evidence file** ·
planted-effect recovery within documented tolerances for F1–F10 · `fetchMore` on 1M rows · ARI on stable vs
unstable clusters · proof labels never reach the model · R reference JSON current (<14 days) · Gate G5 criteria
all satisfied.

---

Language compliance (G-7): 100% English across all code, docstrings, comments, PowerShell scripts, tests,
workflows, commit messages, and reports.

Licence: Proprietary / Internal (`LICENSE` committed; do not modify). `artifacts/review_baseline/` must not be
modified or deleted.
