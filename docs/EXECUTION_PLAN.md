# EnergyBaseline v6 — Phase-Gated Execution Plan

**Status:** ratifications complete (D1–D5 + S7) · Phase 0 **OPEN** · Phase 1 blocked until Phase 0 passes
**Branch:** `arena/af928d3c-energybaseline` · **Base commit:** `2c92f2a` · **Review date:** 2026-11-12
**Companions:** `docs/DECISIONS.md` · `docs/PHASE0_BRIEF.md` · `docs/PROMPT_PHASE0_EXECUTOR.md`

**Branch topology:** the executor works on `arena/3611a52f-energybaseline`; this review branch holds the
governance documents and is the integration point. The reviewer fetches the executor branch read-only,
runs G0 personally, and merges into this branch **only after PASS**. Nobody pushes to another person's
branch, and nothing is force-pushed.

> This is the governance document between the **reviewer** and the **executor**. It is not a product
> artifact. Every document in this repository — product docs, process docs, prompts and reports — is
> English (G-7). There are no translations in-tree; the owner may request one out-of-band, but the
> in-repo text is the only authoritative version.

---

## 0. Verdict (executive summary)

**REJECT for “continue coding on the current state”.** The branch is a working Python prototype, not a
product that meets the prompt. Real strengths exist (AUTO engine with signal/rule logging, a single DB
resolver, a clean `core/` split, working HTML/Excel report generation, green pipeline tests). The distance
to the release-blocking thresholds is structural, not cosmetic:

| # | Release-blocking gap | Violated IDs | Evidence |
|---|---|---|---|
| 1 | No `ProcessPoolExecutor` / Job Object / `freeze_support`; compute runs in a `QThread` **inside the UI process**, and `store.save_run` writes SQLite **from that thread** | SCR-1, SCR-2, SHL-5, LIF-2, AC-03 | `grep ProcessPoolExecutor\|win32job\|freeze_support` → 0 hits; `gui/pages_setup.py::AnalysisWorker(QThread)` |
| 2 | No clean shutdown (`aboutToQuit` / `closeEvent`) | G-8, LIF-1 | 0 hits |
| 3 | **VSG and TCP do not exist** (no UI, no data model, no result cache, no provenance) | §10, §13, PIPE-1..3, AC-23, AC-24 | `grep -i "vsg\|tcp"` → 0 hits |
| 4 | No single-instance, path guard, command registry, i18n or `QSettings` | SHL-1, SHL-3, SHL-4, SHL-6, G-7, AC-04 | 0 hits; `energybaseline/io/paths.py` absent |
| 5 | No `.ebproj`, autosave, checkpoints, backup/restore (online API) or retention; artifacts loaded via `joblib` **without SHA-256** | PER-1..6, AC-18 | `grep ebproj\|zipfile\|checkpoint` → 0 hits; `core/store.py::load_run` |
| 6 | No UQ/conformal, no drift, no causal M&V; the interval level is **hardcoded 0.95** from residual quantiles | UQ-1..5, DR-1..5, T14, AC-15, AC-16, AC-29 | `core/pipeline.py:328` `"level": 0.95`; `grep -i "mapie\|psi\|ks_2samp\|wasserstein\|cusum"` → 0 |
| 7 | No single report JSON model; PDF via **WeasyPrint** (explicitly banned); no artifact-ID narratives, no number↔artifact test, no Glossary/How-to-read/report-diff | REP-1..5, RPT-1..7, G-9, G-11, AC-17, AC-28 | `core/report.py:1`, `requirements.txt` |
| 8 | Web-stack contamination: `package.json`, `next.config.ts`, `src/app/page.tsx`, drizzle, eslint, tsconfig, postcss | G-1, G-2, AC-01, AC-35 | `git ls-files` |
| 9 | No quality infrastructure: no CI, no lock, no `.gitignore`, no `hypothesis`/`pytest-qt`, no R references, no SBOM, no threat model; 19 tests for 7,732 LOC | §27, AC-21, AC-22, PKG-6 | `.github` absent; only `>=` pins |
| 10 | DAT-1 roles/quantity types/inclusion states are unmodelled; aggregation is decided by a **column-name regex** | DAT-1..3, AC-06 | `core/temporal.py::default_agg_for`, `ENERGY_NAME` |
| 11 | Tables are `QTableWidget` capped at 3,000 rows, not `QAbstractTableModel` + `fetchMore` | IPC-7, UX-5, AC-19 | `gui/widgets.py::fill_table(max_rows=3000)` |
| 12 | DB-2/3/4/5/8/10 half-done: `launcher.py` **overwrites** `DATABASE_URL`; `APP_HOME` is not `%LOCALAPPDATA%`; no `portable.flag`; no fallback ladder (`%TEMP%` → in-memory); no pre-migration backup; `busy_timeout`/`synchronous` unset | DB-2..10, AC-05 | `launcher.py:60`, `config/settings.py`, `db/session.py` |

**Process consequence:** phase order follows the prompt (§3) **plus a Phase 0** that closes structural debt
and builds the evidence infrastructure. No phase starts without PASS on the previous one; no PASS is issued
without a machine-readable evidence bundle.

---

## 1. Review method and evidence validity

### 1.1 What the reviewer actually did (reproducible)

```bash
python3 -m venv /tmp/ebv
/tmp/ebv/bin/pip install numpy pandas scipy statsmodels scikit-learn sqlalchemy alembic matplotlib \
    joblib openpyxl jinja2 keyring cryptography pillow pytest pytest-cov pyflakes \
    PySide6 pytest-qt mapie arch sqlglot reportlab
python -m pytest -q
```

Headless Qt harness (widget **logic** only, never native Windows behaviour): link-only stubs for
`libGL/libEGL/libxkbcommon/libdbus` derived from `nm -D --undefined-only` on `libQt6Gui`/`libqoffscreen`
(xkb tagged `V_0.5.0`), plus `QT_QPA_PLATFORM=offscreen` and `LD_LIBRARY_PATH`.
This harness is a review tool, never shipped evidence.

### 1.2 Three evidence levels

| Level | Definition | Used for |
|---|---|---|
| **E1** | Raw command output in `artifacts/phaseX/*.txt` + commit SHA-256 | everything |
| **E2** | Machine-readable JSON produced by the product itself (`system.health`, `system.sbom`, AUTO-log hash) | DB-*, AUTO-*, TCP-* |
| **E3** | Evidence from a **real Windows 10 22H2 / Windows 11 x64 machine**: logs, timings, screenshots, binary SHA-256 | LIF-1/2, SCR-1/6, PKG-1..6, native UX-* |

**Hard rule:** any `[M]` ID that is only provable with E3 and has no E3 ⇒ **FAIL**, even if the code looks right.
This sandbox runs no Windows, no Job Object, no NSIS, no Authenticode, no Windows PyInstaller. Verbal claims are rejected.

---

## 2. Prompt claims vs. actually installed libraries (§0 library-verification policy)

| Prompt claim | Reality in the installed version | Required action |
|---|---|---|
| `statsmodels.imputation.mice.MICEData(df).mcar_test()` | **Absent** (statsmodels 0.15.0) → `AttributeError` | Native Little's MCAR + R reference (`MissMech::mcartest`) |
| `statsmodels.tsa.stattools.acorr_ljungbox` | It is `statsmodels.stats.diagnostic.acorr_ljungbox` | Fix the import; log the deviation |
| `grangercausalitytests(..., verbose=False)` | `verbose` removed in 0.15 → `TypeError` | Drop the argument; add a version-compat test |
| — | statsmodels 0.15 warns that `adfuller`/`kpss`/`acorr_lm` change return types (`result_object`) | Exact pins + explicit `result_object` usage |
| MAPIE v1, five classes + `TimeSeriesRegressor(method="enbpi"/"aci")` | **Correct** (mapie 1.6.0): all five present, `method` defaults to `'enbpi'` | Add to requirements (currently absent) |
| `arch` for PP / DF-GLS / Zivot–Andrews | **Correct** (arch 9.0.0) | Add to requirements (currently absent) |
| `sqlglot.parse` (not `parse_one`) | **Correct** (30.31.0): `parse("SELECT …; DROP …")` → two statements `[Select, Drop]` | Replace the regex `validate_select` with an AST guard |
| ReportLab for PDF | **Correct** (5.1.0) | Install; delete WeasyPrint |
| `scipy.stats.brunnermunzel` | Present, but `midrank` is deprecated → `variant` (SciPy 1.19) | Use `variant` |
| Shapiro for n > 5000; KPSS interpolation | Real limitations/warnings | Documented subsampling (TC-8) |

**Consequence:** 30 `>=` pins and no lock contradict AUTO-5 and PKG-6. **Exact pins + a hashed lock are mandatory.**

---

## 3. Blocking decisions (RATIFIED 2026-11-12)

| # | Question | Ratified |
|---|---|---|
| D1 | Authenticode signing | **B** — defer + build/verify the signing pipeline (self-signed CI test); `PKG-3 → [S]`; dated trigger before any external distribution |
| D2 | E3 reference machine | **B+A** — mandatory `windows-latest` CI **plus** a physical reference machine (`8-core · 16 GB · NVMe · Win11 23H2 · 1920×1080@100 %`) for all normative numbers |
| D3 | NN stack placement | **A** — Full install by default (pinned CPU torch) + optional Lite build; AC-10 is proven on Full |
| D4 | R reference validation | **B** — versioned JSON with the R script hash + a nightly R job that opens an issue on divergence |
| D5 | PDF engine | **A** — **ReportLab only**; WeasyPrint, Qt-text-PDF and matplotlib-PDF removed and gate-banned |
| S7 | License | **Proprietary / Internal** (`LICENSE` committed; do not modify) |

Full options, costs, rejected alternatives and consequences: `docs/DECISIONS.md`.

Secondary defaults (adopted, objection window open until G0): S1 Python 3.11.9 · S2 no history rewrite ·
S3 `spawn` pool with file-backed payloads · S4 `ruff` + `pyflakes` · S5 coverage 60 % now, 75 % for
statistics/UQ modules from Phase 4 · S6 generated sample artifacts.

---

## 4. How to report, how a verdict is issued

### 4.1 Iron rules for the executor

1. Work only on your own branch (`arena/3611a52f-energybaseline`); never the reviewer's branch, never
   `main`; no force-push.
2. **Never align a test with a bug.** Editing or deleting an existing test to go green = instant FAIL.
3. Every `[M]` ID needs an automated test or a documented manual check **with an evidence file**.
4. Every claim needs an artifact path (file, SHA-256, command line).
5. Every number, metric, p-value and statistical statement comes from an artifact (G-9); no hardcoded metric in templates.
6. No web/JS/Electron/Tauri/HTTP dependency; no `QWebEngineView`; no WeasyPrint anywhere.
7. Data is never deleted or modified without an Action Policy and a log entry (G-6).
8. If you cannot prove something, say “not proven” — concealment is more expensive than a defect.
9. Every artifact is written in English, including commit messages and test names (G-7).

### 4.2 Mandatory end-of-phase report (`artifacts/phaseX/REPORT.md` + chat summary)

```
1) Phase ID, full commit SHA, git diff --stat
2) ID → file/function → test table (one row per ID)
3) Commands executed and raw output paths
4) pytest -q summary + coverage of the touched modules
5) Artifacts (report/dashboard/log/DB/installer) + SHA-256
6) E3 (Windows): logs/timings/screenshots + reference hardware
7) Deltas in deviations / assumptions / not_implemented
8) What was not proven and why
9) Open risks and their effect on the next phase
```

### 4.3 Verdict formats

- **PASS** — every gate green with E1/E2/E3.
- **PASS-WITH-CONDITIONS** — `[S]/[O]` only; conditions numbered and time-boxed.
- **FAIL + defect list** — each defect: `D-xx`, violated ID, evidence, required fix, test to add.
- Repair loop: **max 3 rounds**; a fourth round stops the line and escalates to the owner with a risk report.

### 4.4 Auto-fail triggers

Test claims without raw output · mock-centric tests standing in for `[M]` IDs · test tampering · hardcoded
metrics in report templates · Windows claims without E3 · adding a web stack · silent data mutation ·
dynamic `eval`/`getattr` in the dispatcher · loading pickle without SHA-256.

### 4.5 Definition of Done (shared by all phases)

`pytest -q` green · `pyflakes` clean · `requirements_matrix.md` updated (no `done` row without tests) ·
`deviations.md` updated · a runnable artifact (not a mock) · no regression in earlier phase tests.

### 4.6 Canonical Gate G1 block (byte-identical with `docs/PHASE0_BRIEF.md`)

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
```

---

## 5. Phase plan

Order respects technical dependency: without lifecycle/process-pool and without VSG/TCP, every later
modelling decision would be rewritten. Estimates are engineer-weeks.

| Phase | Title | Depends on | Estimate |
|---|---|---|---|
| 0 | Truth & Rails (structural debt + evidence infrastructure) | — | 1–1.5 |
| 1 | Lifecycle & Skeleton (M-1 rebuilt) | 0 | 3–4 |
| 2 | Data / Time / Column Setup | 1 | 2–3 |
| 3 | VSG + TCP + Cache (compliance core) | 2 | 4–5 |
| 4 | Test catalog T1–T14 | 3 | 6–8 |
| 5 | EDA / Mining F1–F10 + large-data performance | 3,4 | 4–5 |
| 6 | Modelling / Validation / Scoring / Top-5 / Lock | 3,4,5 | 4–6 |
| 7 | UQ + Drift + Prediction & Monitoring + Causal M&V | 6 | 4–5 |
| 8 | Reporting & Audit depth | 4,5,6,7 | 3–4 |
| 9 | Persistence / UX / Security / Packaging / Release | 1,8 | 3–4 |

**Critical path:** 0 → 1 → 3 → 4 → 6 → 7 → 8 → 9.

---

### Phase 0 — Truth & Rails

**Goal:** turn the repository into an auditable workshop. **No product features.** Scope: G-1, G-2, G-7,
§27, AC-21, AC-22, PKG-6 + the consequences of D1–D5 + S1..S6. Full work items: `docs/PHASE0_BRIEF.md`.

Deliverables in short: delete the web stack · exact pins + hashed lock + drop WeasyPrint · three AST/behaviour
gates each with a negative test · two-OS CI (`windows-latest` mandatory) · generated requirements matrix ·
process documents (deviations/assumptions/not_implemented/SECURITY/THIRD_PARTY_NOTICES/SUPPORT_MATRIX/CHANGELOG) ·
repo hygiene · the two broken tests resolved **by contract** · seeds for the next phase (no features).

**Gate G0** — the canonical block lives in `docs/PHASE0_BRIEF.md` §“Gate G0” and is reproduced
verbatim in `docs/PROMPT_PHASE0_EXECUTOR.md` §7. Three copies of a gate that can drift are a defect:
if the block changes, all three files change in the same commit (the reviewer diffs them).

**How the reviewer rejects:** a gate without a negative test; a matrix row `done` with empty `test_ids`;
fixing a broken test with `skip`/`xfail`/deleted assertion; any `weasyprint` left in code or manifests;
any Next/React file still tracked; a lock without hashes.

---

### Phase 1 — Lifecycle & Skeleton (M-1 rebuilt)

**Goal:** the non-negotiable spine — the UI never computes, Windows never leaks a process, the DB never
blocks boot. Scope: §6 (SHL-1..9, IPC-1..7, LIF-1/2/4/5), §8 (DB-1..11), SCR-1..4, PKG-1/2/6,
AC-03, AC-04, AC-05.

**Key structural move (R-1):** move `run_analysis` out of `QThread` into a worker process.
`ProcessPoolExecutor(max_workers=min(CPU//2, tasks))` + `freeze_support()` + `spawn`; workers never open
SQLite (SHL-5); results return over `multiprocessing.Queue`; payloads >1 MiB travel as
`{path, sha256, rows}` under `APP_HOME/cache/`; cancellation tokens at stage boundaries **and** inside
epoch/trial/fold/test callbacks; a worker that ignores cancel for 10 s is killed.

**Deliverables:** command registry with a compile-time allowlist (SCR-7 only, no dynamic dispatch) ·
`io/paths.py` guard (APP_HOME, picked paths, temp; reject `..`, UNC, symlinks) · pool handshake with
readiness timeout (10 s warm / 30 s first) and protocol versioning · `aboutToQuit` → cancel → join 5 s →
hard stop → checkpoint → DB close; Windows Job Object with `KILL_ON_JOB_CLOSE` · `QLocalServer`
single-instance · full DB fallback ladder (`%LOCALAPPDATA%` or `portable.flag` → `%TEMP%` with a
“data may be lost” banner → in-memory with a red banner), 3 s external probe with audit + banner, complete
SQLite pragmas, pre-migration backup via the online backup API with restore on failure, removal of the
launcher's env override · real Status page · PyInstaller **onedir** + NSIS/Inno per-user + `.lnk` + SBOM.

**Gate G1:** DB fallback matrix tests (no env, whitespace env, unreachable Postgres, read-only APP_HOME,
`%TEMP%` rung, in-memory rung, sentinel self-test, registry rejection, no-orphan script).
**E3 required:** `scripts/evidence/lif_orphans.ps1` (0 orphans ≤ 5 s), `startup_time.ps1` (5 cold + 5 warm,
median/p95), `installer_smoke.ps1`, `dpi_matrix.ps1` — each emits JSON.

**How the reviewer rejects:** compute still in a UI thread; `store.*` called from a worker; `aboutToQuit`
only logging instead of joining; a Job Object created but never assigned; no UI banner on fallback;
missing test IDs for DB-11; installer never tested per-user.

---

### Phase 2 — Data / Time / Column Setup

Scope: §9 (DAT-1..8), AC-06.

**Deliverables:** role/quantity-type/unit/availability modelled **in data** (`time|target|predictor|
static_factor|group|weight|ignore`; `flow|stock|rate|quality|index`; `known_in_advance|observed_only`),
persisted in the project/DB · aggregation driven by **quantity type** (flow→sum, stock→last, rate→
time-weighted, quality→mean/median, index→mean); the name-based heuristic becomes a *suggestion* only,
logged · `DATA_REFUSED` for coarser data with an explanation; never disaggregate · explicit DST handling
(23/25-hour days, duplicated and missing hours, UTC stored / local displayed) · Non-Routine Events table ·
Outlier Action Policy (`flag_only|ask_user|auto_apply`) recording rule, rows and the training-set delta ·
streamed CSV/Excel/Parquet ingest with encoding/decimal detection and a Parquet cache.

**Gate G2:** hypothesis-based DST tests across four zones (including one without DST) · refusal test ·
quantity-type × aggregation matrix · all three Action Policy modes · Parquet/encoding · DAT-5
“no fabrication” (no synthetic rows, no target imputation inside an evaluation set).

**Rejected if:** aggregation still derives from a column-name regex · roles live only in the UI and are
not persisted · DST is tested against a single zone.

---

### Phase 3 — VSG + TCP + Cache (compliance core)

Scope: §10 (VSG-1..9), §12 (AUTO-1..6), §13 (TCP-1..14), PIPE-1..3, AC-08, AC-23, AC-24, AC-33.

**Why before any statistics:** without VSG/TCP every test would be rewired later, and PIPE-1 forbids any
inferential test before the gate is passed.

**Deliverables:** per-target × per-variable inclusion state with AUTO default and logged resolution ·
pre-test profile (missing %, unique, variance, constant/near-constant, leakage suspicion, availability,
mini-histogram) and an Impact Preview (variables, tests, family sizes, Fast vs Full estimate) · bulk
actions, presets, cross-target copy, descriptive profile for `exclude` · **non-disableable** integrity
checks (leakage MT-2, time order, DST/duplicates, target never a predictor) with conflict display ·
provenance (`source`, `phase ∈ pre_test|post_test`, `post_hoc` flagged in Limitations) · full TCP (α
0.001–0.20 with presets and a warning above 0.10; confidence 80/90/95/99/custom; five multiple-testing
corrections storing **both** raw and adjusted p; family definition; decision rule; per-test panels;
variable × family scope matrix; expected-sign editor; gate-role/action-policy editor; guard rails;
pre-registration lock with a hash) · result cache keyed by
`(data_hash, variable_set, test_id, parameters, seed, library_versions)` · **what-if re-evaluation with
zero recomputation** · decision-sensitivity table · AUTO-log inspector plus a deterministic AUTO-log hash.

**Gate G3:** cache-key invalidation recomputes only dependent stages · what-if changes α/correction/rule
with **0** engine calls (assert on a call counter) but correct flips · hypothesis fuzzing of TCP/VSG ·
proving integrity rules cannot be disabled · identical AUTO-log hash across two runs.

**Rejected if:** tests run before VSG/TCP are accepted · adjusted p is not stored · what-if recomputes ·
`post_hoc` appears only in the UI · “AUTO” resolves to a hidden constant.

---

### Phase 4 — Test catalog T1–T14

Scope: §14 (T1..T14, TC-1..8), §15 (CI-1..4), §27 (R references), AC-09, AC-25, AC-26.

**Deliverables:** one contract per test (`statistic, p_raw, p_adj, df, effect_size, ci, n_used,
assumptions_checked, decision, interpretation, downstream_action, mode`, library versions) · T1–T9 and T13
pre-model, T10–T12 post-model on OOF results, T14 only with a `group` column · TC-1..TC-8 (declared
assumptions with logged robust/non-parametric fallback, HAC/block bootstrap with “naive” labelling,
stationarity decision matrix, bootstrap p for Quandt–Andrews, **native** Little's MCAR, flag-only
influence, documented subsampling for large n) · custom tests with R references (Chow, Quandt–Andrews,
Pettitt, Diebold–Mariano/HLN, Nemenyi, Little's MCAR, MCS) · Type I error Monte Carlo ≈ α and power against
planted effects · every key estimate carries a CI at the selected confidence level with a stated method.

**Gate G4:** at least one passing and one failing case per T-ID · “not run (user)” appears in the report ·
no “observed power” · “naive” labelling on autocorrelated series · stale R reference detection.
Sub-gates: G4a T1–T4, G4b T5–T9, G4c T10–T14.

**Rejected if:** a custom test ships without an R reference · adjusted p is reported where raw is meant (or
vice versa) · `p_and_effect_size` is claimed while effect size is ignored in the decision.

---

### Phase 5 — EDA / Mining F1–F10 + large-data performance

Scope: §16, UX-5/UX-8 (tables), IPC-7, AC-27.

**Deliverables:** F1–F9 with the specified charts/tables · F8 consensus via Kendall's W · F9 with a
transparent formula and per-variable breakdown · F10 optional automated EDA attach labelled “descriptive,
not inferential” · clustering with validity indices **and bootstrap ARI**; regime labels are never model
inputs (MT-5) · tables move to `QAbstractTableModel` + `fetchMore` + `QSortFilterProxyModel`; the 3,000-row
ceiling disappears from data paths; LTTB/M4 downsampling for large charts · a 1M-row benchmark.

**Gate G5:** planted-effect recovery within documented tolerances · `fetchMore` on 1M rows · ARI on stable
vs unstable clusters · proof that labels never reach the model.

**Rejected if:** clustering ships without validity/stability · large tables remain `QTableWidget` ·
method comparison lacks rank agreement.

---

### Phase 6 — Modelling / Validation / Scoring / Top-5 / Lock

Scope: §17, §18, MT-1..5, VAL-1..7, SC-1..10, AC-07, AC-10..13, AC-31, AC-32.

**Deliverables:** the full `[M]` portfolio (mean, degree-day, MLR, Ridge, Lasso, EN, piecewise 1P–5P, TOWT,
RF, ExtraTrees, GB, XGBoost, LightGBM, SVR-RBF, MLP, one sequence NN) with `[S]/[O]` tracked · leakage-safe
validation where the purge gap is **max(1 day, 1 step, max lag, max rolling window)** (today it is 1 day),
in-fold preprocessing/selection, nested HPO (already in-fold — keep and test), final holdout · NN gate with
a logged reason · ensembles only with >3 % gain and **NNLS on inner-fold OOF** evaluated on outer folds ·
13-criteria scoring with weights, ±20 % sensitivity, deterministic tie-breaks, separate Rejected /
Qualified-not-shortlisted registers, SC-9 handling, explicit user acknowledgement · Top-5 with SC-7
representations (closed-form equations for parametric models; architecture + hyperparameters + **labelled
surrogate with fidelity R²** for non-parametric) · lock creates an immutable versioned baseline with
artifact SHA-256, features, pipeline, VSG/TCP snapshots, window, thresholds, seed, calibration summary,
plus a model card.

**Gate G6:** purge grows with a larger lag · scaler/imputer/selector fit inside folds · `k_max=min(p,n/20)` ·
NN gate both ways · NNLS weights fitted on inner folds only · deterministic tie-break order · SC-4 rank
stability · lock immutability and audit trail · Excel-formula round-trip numerics.

**Rejected if:** Top-5 is not visible before lock or lock is mutable · rejected models are mixed with
“merely lower score” · the purge gap stays at one day · the surrogate is presented as the model's formula.

---

### Phase 7 — UQ + Drift + Prediction & Monitoring + Causal M&V

Scope: §19, §20, §21, T14, AC-14..16, AC-29, AC-30.

**Deliverables:** conformal by default via MAPIE 1.6 (`SplitConformalRegressor`,
`CrossConformalRegressor`, `JackknifeAfterBootstrapRegressor`, `ConformalizedQuantileRegressor`,
`TimeSeriesRegressor(method="enbpi"|"aci")`), coverage from TCP-1, labelled quantile/bootstrap fallback ·
calibration report (empirical vs nominal coverage, interval width, Winkler, conditional coverage by regime/
season/load, Kupiec and Christoffersen, PIT only for distributional models) · PM-1..7 (per-target unlock,
file/Excel/Parquet with schema validation and extrapolation warnings, **SQL guard via `sqlglot.parse`** —
single statement, root `Select/Union/Intersect/Except`, reject DDL/DML and
`xp_cmdshell|OPENROWSET|LOAD_FILE|INTO OUTFILE|COPY|ATTACH|PRAGMA`, read-only, row limit, ~10 s timeout,
TLS on; credentials in keyring/DPAPI and never in `.ebproj`/logs/exports; manual entry and paste; per-
timestamp outputs with a stated sign convention and NaN guards; auto alert `max(10 %, 2×CV(RMSE))` with
persistence and hysteresis; in-window dashboard, exports and snapshots tied to the baseline version and
input hash) · drift (PSI with training bins, KS, Wasserstein, chi², CUSUM/EWMA/Page–Hinkley on standardised
residuals, rolling CV(RMSE)/NMBE, traffic light, **re-validation recommendation only — never retrain**) ·
causal M&V for datasets with a `group` column (T14 balance, parallel trends, propensity overlap, kernel
balancing, DoWhy/EconML effect with refutation, reported as “possible savings / non-routine event”).

**Gate G7:** empirical coverage ≈ nominal within tolerance · Kupiec/Christoffersen on synthetic series ·
“no credential in `.ebproj`/logs/report” (grep + ZIP inspection) · fuzz corpus 100 % accept/reject · snapshot
tied to the baseline version · PSI/KS on shifted data · drift never mutates a model.

**Rejected if:** “distribution-free guarantee” is claimed for autocorrelated series · SQL validation is
still regex · passwords persist to disk · drift is wired to retraining.

---

### Phase 8 — Reporting & Audit depth

Scope: §23 (REP-1..5, RPT-1..7), G-9, G-11, AC-17, AC-28.

**Deliverables:** **one JSON report model** as the single source for in-app, PDF and Excel · PDF via
**ReportLab**, Excel via openpyxl, in-app preview via matplotlib (Agg static, QtAgg interactive) — no
`QWebEngineView` · every REP-2 section including the EDA Atlas, Correlation Atlas, Full Test Results
Appendix, Decision-Sensitivity Summary, Rejected/Not-Shortlisted registers, AUTO log, model cards, drift,
reproducibility, glossary and audit appendix · RPT-1 template for every section
(Purpose→Method→Settings→Results→Charts→Interpretation→Decision→Caveats) · three depth levels with linked
TOCs · chart standards with units, n, CI bands, colour-blind-safe palette and alt text · a deterministic
template narrative where every sentence carries an **artifact ID**, enforced by a build-failing
number↔artifact test · report diff and Excel-formula export as `[S]`.

**Gate G8:** numerical agreement across the three renderers for 20 key metrics · an empty-data report still
renders · alt-text and TOC tests · “no hardcoded metric in templates” · 100 % of numeric sentences carry IDs.

**Rejected if:** any renderer reads `RunResult` directly · a narrative sentence lacks an artifact ID ·
WeasyPrint or Qt produces the PDF · the Executive depth exceeds 10 pages.

---

### Phase 9 — Persistence / UX / Security / Packaging / Release

Scope: §22 (PER-1..6, DOM-1..3), §24 (UX-1..12), §25, §26, AC-02, AC-18..22, AC-30, AC-33..35.

**Deliverables:** `.ebproj` ZIP with a manifest (dataset relative path + SHA-256 + size + rows, config/VSG/TCP
with provenance, AUTO log, selections, snapshots, artifact hashes, model cards, **no credentials**), relink
dialog and schema migration · autosave every 5 minutes and on debounced change with atomic writes, stage
checkpoints and resume · backup via the **SQLite online backup API** with a hash manifest and validated
restore; retention with a dry run, and locked-baseline artifacts are never purged · PER-6 artifact safety
(ONNX/skops/JSON preferred; pickle only after SHA-256 verification and a user warning) · UX (locked
stepper, Ctrl+K command palette over the registry, QSS themes persisted in `QSettings`, accessibility with
`:focus`, `QAccessible`, 80–200 % font scaling and high contrast, toasts/empty states/skeletons, taskbar
progress, drag & drop, tray notifications, i18n-ready `QTranslator`) · `SECURITY.md` threat model, log
scrubbing test, SBOM shipped, `pip-audit` in CI · NSIS/Inno per-user installer, `.ebproj` association,
uninstaller that offers to keep data, signed updater (explicit user action only), checksums, reproducible
build · final English docs including “Where is my data stored?”. The UI is *i18n-ready* (`QTranslator`) but
ships **English only** — no localized `.qm` files are produced (G-7).

**Gate G9:** full end-to-end run on Windows (E3): install → ingest sample → VSG → TCP → run → Top-5 → lock →
monitoring → report → backup/restore → uninstall keeping data; then an **AC-01..AC-35 sweep** with evidence
per item, accessibility sweep, i18n smoke, single-instance, association, resume-after-kill, zero orphans.

**Rejected if:** any AC lacks evidence · the matrix says `done` while the test is missing · the uninstaller
deletes data · the updater runs during boot.

---

## 6. AC → phase seed map

| AC | Phase | AC | Phase | AC | Phase |
|---|---|---|---|---|---|
| AC-01, AC-02, AC-04, AC-05 | 0/1 | AC-12, AC-13, AC-31, AC-32 | 6 | AC-22, AC-30, AC-33 | 0/9 |
| AC-03 | 1 | AC-14, AC-15, AC-16, AC-29 | 7 | AC-34, AC-35 | 7/0 |
| AC-06 | 2 | AC-17, AC-28 | 8 | AC-23, AC-24 | 3 |
| AC-07, AC-08, AC-10, AC-11 | 3/6 | AC-18, AC-19 | 9 | AC-09, AC-25, AC-26 | 4 |
| AC-20, AC-21 | 1/9 | AC-27 | 5 | | |

---

## 7. Top risks and traps

1. **R-1 (highest):** moving compute into a process pool after the UI is finished forces a wide rewrite.
   Mitigation: in Phase 1 move only the *transport* (`run_in_pool(payload)` adapter); leave the pipeline untouched.
2. **R-2:** “AUTO everywhere” degenerating into hidden constants. Mitigation: a test that every setting has an
   AUTO state and an emitted resolution (AC-33).
3. **R-3:** test inflation — tests asserting their own mocks. Mitigation: rule 4.2 and an import/monkeypatch audit.
4. **R-4:** a beautiful report with no provenance. Mitigation: the number↔artifact test and “no hardcoded metric”.
5. **R-5:** dependency drift (statsmodels 0.15 changed return types). Mitigation: lock + explicit `result_object`.
6. **R-6:** no Windows evidence ⇒ unprovable LIF/PKG claims. Mitigation: mandatory E3 + `scripts/evidence/`.
7. **R-7:** T1–T14 breadth (100+ tests) causing attrition. Mitigation: T-ID matrix and sub-gates G4a/G4b/G4c.
8. **R-8:** WeasyPrint or Node creeping back for convenience. Mitigation: gate scripts from Phase 0.

---

## 8. Next step for the executor

1. ~~Ratify D1..D5~~ ✅ done (2026-11-12); S7 also ratified.
2. Execute **Phase 0** exactly per `docs/PHASE0_BRIEF.md` / `docs/PROMPT_PHASE0_EXECUTOR.md` and submit
   `artifacts/phase0/REPORT.md` in the §4.2 format.
3. **Stop** for the reviewer's verdict; then Phase 1. No phase self-opens.

> The reviewer writes no code and fixes nothing. The reviewer reads, runs, measures evidence and votes.
> No phase is approved because it “looks like it works”.

---

## Appendix A — Reproducing the review environment (sandbox)

```bash
python3 -m venv /tmp/ebv
/tmp/ebv/bin/pip install -r requirements.lock          # after Phase 0; today: requirements.txt
QT_QPA_PLATFORM=offscreen LD_LIBRARY_PATH=/tmp/qstub /tmp/ebv/bin/python -m pytest -q
```
Link-only Qt stubs exist only to make `HeadlessQt` importable in CI; they are **never** accepted as
evidence of native Windows behaviour.

## Appendix B — Reviewer checklist for every phase

- [ ] Commit SHA and `git diff --stat` match the report.
- [ ] Every matrix ID has code + test + evidence; no `done` without a test.
- [ ] `pytest -q` was actually run (raw output, not a handwritten summary).
- [ ] No `[M]` test asserts its own mock/monkeypatch.
- [ ] Gate scripts green (web-free, env-readers, test-integrity).
- [ ] Artifacts present with SHA-256.
- [ ] E3 present for Windows IDs (otherwise FAIL).
- [ ] deviations / assumptions / not_implemented updated.
- [ ] No regression in previous phase tests.
