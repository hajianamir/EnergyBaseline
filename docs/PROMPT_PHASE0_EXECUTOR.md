# PROMPT — Executor Agent · EnergyBaseline v6 · Phase 0 (Truth & Rails)

> **Copy this whole file to the executor agent.** Reference documents (read first, mandatory):
> `docs/EXECUTION_PLAN.md`, `docs/DECISIONS.md`, `docs/PHASE0_BRIEF.md`.

---

## 1. Role and rules of engagement

You are the **executor**, not the judge. An independent reviewer reads your code, re-runs your commands
personally, and issues PASS/FAIL. Three things fail you immediately: a claim without raw output,
tampering with tests, and adding anything outside the phase scope.

- **Branch:** `arena/af928d3c-energybaseline` — the only branch you may touch. Never touch `main`. No force-push.
- **Base commit:** `b0dc49d` (on top of `2c92f2a`).
- **Scope of this round: Phase 0 only.** Do not add product features: no VSG, no TCP, no test catalog,
  no ProcessPool, no new report, no new UI. Anything extra is rejected.
- **Language:** code, product docs, commit messages and reports **in English** (G-7). Conversation with the owner is Persian.
- **After delivering the report: STOP.** Phase 1 does not open until the reviewer votes.

---

## 2. Verified repository state (measured by the reviewer — do not argue, do not re-litigate)

| Metric | Value today |
|---|---|
| `pytest -q` | **2 failed, 17 passed** — `test_nn_explicitly_rejected_for_small_data`, `test_daily_multitarget_independent` |
| AST violations (banned imports) | **2** — `weasyprint` in `energybaseline/core/report.py` |
| Manifests | `weasyprint` still declared in `requirements.txt` |
| Web stack tracked by git | `package.json`, `next.config.ts`, `tsconfig.json`, `postcss.config.mjs`, `eslint.config.mjs`, `drizzle.config.json`, `src/**` |
| Absent (hits = 0) | `ProcessPoolExecutor`, `freeze_support`, `aboutToQuit`, `closeEvent`, `QLocalServer`, `QSettings`, `QTranslator`, `ebproj`, `mapie`, `arch`, `sqlglot`, `reportlab`, `vsg`, `tcp`, `QAbstractTableModel`, `fetchMore`, `QAccessible`, `hypothesis`, `pytest-qt`, `onnx`, `cyclonedx` |
| Also missing | CI, lock file, R reference suite, SBOM, `SECURITY.md`, `.gitignore` (partially added by reviewer) |
| Size | 7,732 Python LOC · 19 tests · 75 tracked files |

**Library facts the reviewer verified against installed versions — record them in `docs/deviations.md`:**
- `statsmodels 0.15.0`: `MICEData.mcar_test()` **does not exist** (`AttributeError`) → custom implementation + R reference in Phase 4.
- `statsmodels 0.15.0`: `acorr_ljungbox` lives in `statsmodels.stats.diagnostic`, **not** `tsa.stattools`.
- `statsmodels 0.15.0`: `grangercausalitytests(verbose=...)` raises `TypeError`; `adfuller`/`kpss`/`acorr_lm` emit
  `FutureWarning` about the `result_object` return type.
- `mapie 1.6.0` (`SplitConformalRegressor`, `CrossConformalRegressor`, `JackknifeAfterBootstrapRegressor`,
  `ConformalizedQuantileRegressor`, `TimeSeriesRegressor(method='enbpi'|'aci')`), `arch 9.0.0`
  (`ADF/KPSS/PhillipsPerron/DFGLS/ZivotAndrews`), `sqlglot 30.31.0` (`parse` returns one AST per statement),
  `reportlab 5.1.0` — all exist; the prompt's claims hold.
- `scipy 1.17`: `brunnermunzel(midrank=...)` is deprecated → `variant`.

---

## 3. Ratified decisions (non-negotiable in this round)

| ID | Decision |
|---|---|
| **D1 = B** | Signing deferred; `PKG-3 → [S]`; sign script built and verified with a self-signed cert; a dated **trigger** ("before any distribution outside developer machines") is recorded |
| **D2 = B+A** | CI on `windows-latest` is mandatory **plus** a physical reference machine: `8-core x64 · 16 GB · NVMe · Windows 11 23H2 · 1920×1080 @100 %` |
| **D3 = A** | Default install is **Full**; optional **Lite** build (`build_exe.py --profile full\|lite`) |
| **D4 = B** | R reference as versioned JSON + a nightly R job; **this phase: skeleton and format only** |
| **D5 = A** | **ReportLab only**; WeasyPrint / Qt-text-PDF / matplotlib-PDF are removed and gate-banned |
| **S1** | Python **3.11.9** (`requires >=3.11,<3.12`) |
| **S2** | No history rewrite; forward deletion only |
| **S3** | `spawn` process pool with file-backed queues >1 MiB (Phase 1 note only) |
| **S4** | `ruff` (lint + format) and `pyflakes` in CI |
| **S5** | Coverage gate `--cov-fail-under=60` on `energybaseline/core` |
| **S6** | `samples/*.xlsx` are generated artifacts, never committed |
| **S7** | License **Proprietary / Internal** — `LICENSE` already added; do not modify or delete it |

---

## 4. Phase 0 work items (D0.1 … D0.10)

### D0.1 — Remove the web stack
```bash
git rm -r package.json package-lock.json next.config.ts next-env.d.ts tsconfig.json \
         postcss.config.mjs eslint.config.mjs drizzle.config.json src/
```
Strip every Node/npm/Next/Tailwind/drizzle reference from `README.md`, `install.bat`, `start.bat`, `stop.bat`.
Keep the sentence: “Python-only · PySide6 · offline · no web server · no browser”.

### D0.2 — Dependencies: pins, lock, remove WeasyPrint
- Create `requirements.in`; produce `requirements.txt` with **exact `==` pins** and `requirements.lock`
  generated with `--generate-hashes` (pip-tools or uv).
- **Add** (currently imported but undeclared, or required): `joblib`, `mapie==1.6.0`, `arch==9.0.0`,
  `sqlglot==30.31.0`, `reportlab==5.1.0`, `pydantic` (pinned), `hypothesis`, `pytest-qt`, `ruff`,
  `cyclonedx-bom`, `pip-audit`; `pywin32` behind a Windows marker.
- **Remove** `weasyprint` everywhere.
- `docs/SUPPORT_MATRIX.md`: Python 3.11.9, Windows 10 22H2 / 11 x64, the D2 reference hardware,
  and the pinned CPU-only torch wheel (Full build).

### D0.3 — Three gates, each with a negative test
| Script | Behaviour |
|---|---|
| `scripts/gates/guard_web_free.py` | **AST scan** over every `*.py` for banned imports (`weasyprint`, `PySide6.QtWebEngineWidgets`, `flask`, `fastapi`, `uvicorn`, `http.server`); scans manifests; rejects web-stack files in `git ls-files` |
| `scripts/gates/guard_env_readers.py` | Only `energybaseline/config/settings.py` may read `DATABASE_URL` / `POSTGRES_URL` / `DB_CONNECTION_STRING` / `DB_URI` / `EB_DATABASE_URL` |
| `scripts/gates/guard_tests_integrity.py` | SHA-256 of baseline tests in `tests/.baseline.sha256`; modified/removed test without authorization ⇒ error |

**Required:** `tests/gates/` with one negative test per gate (temp file with `import weasyprint` ⇒ exit≠0;
`os.environ["DATABASE_URL"]` in any other module ⇒ exit≠0; a one-byte edit of a test ⇒ exit≠0).
**Documented exemption:** `docs/EXECUTION_PLAN.md`, `docs/DECISIONS.md`, `docs/PHASE0_BRIEF.md`,
`docs/PROMPT_PHASE0_EXECUTOR.md` and `artifacts/**` only *quote* the words and are not scanned.
This exemption belongs in the script's docstring, not only in the report.

### D0.4 — CI
`.github/workflows/ci.yml` with matrix `[windows-latest, ubuntu-latest]`, in this order:
lock install → `ruff check .` + `ruff format --check .` → `pyflakes` → three gates →
`pytest -q --cov=energybaseline/core --cov-fail-under=60` → `pytest-qt` smoke → `pip-audit` →
`cyclonedx-py` → upload `sbom.json`, `coverage.xml`, `artifacts/**`.
`.github/workflows/r-reference.yml`: **skeleton only** (nightly + `workflow_dispatch`); execution lands in Phase 4.

### D0.5 — Generated requirements matrix
- `docs/requirements.yaml` containing **every** ID: `AC-01..AC-35`, `DB-1..11`, `SHL-1..9`, `IPC-1..7`,
  `LIF-1..5`, `SCR-1..7`, `DAT-1..8`, `VSG-1..9`, `AUTO-1..6`, `TCP-1..14`, `T1..T14`, `TC-1..8`, `CI-1..4`,
  `F1..F10`, `VAL-1..7`, `SC-1..10`, `UQ-1..5`, `PM-1..7`, `DR-1..5`, `MT-1..5`, `PER-1..6`, `REP-1..5`,
  `RPT-1..7`, `UX-1..12`, `DOM-1..3`, `PKG-1..6`, plus `D0.1..D0.10`.
- Each row: `id, priority(M/S/O), phase, code_refs[], test_ids[], status`.
- `scripts/gen_matrix.py --check` must enforce: every ID present · no `status: done` with empty `test_ids` ·
  no AC without a phase.
- Output `docs/requirements_matrix.md` with header “generated — do not edit”.

### D0.6 — Process documents (English)
`docs/deviations.md` (the 10 baseline gaps + the library deviations above + **PKG-3 → [S] with its trigger**) ·
`docs/assumptions.md` (S1..S7) · `docs/not_implemented.md` · `SECURITY.md` (threat model: local attacker,
malicious data file, SQL injection, artifact tampering) · `THIRD_PARTY_NOTICES.md` ·
`docs/SUPPORT_MATRIX.md` · `CHANGELOG.md`.

### D0.7 — Repository hygiene
`.gitignore` must cover `data/`, `logs/`, `artifacts/**/*.db|sqlite|joblib`, `*.joblib`, `*.ebproj`, `build/`,
`dist/`, `_internal/`, `EnergyBaseline.exe`, `*.spec`, `__pycache__/`, `.venv/`.
Verify: zero `.pyc` / `__pycache__` tracked by git.

### D0.8 — The two broken tests: resolve by contract, not by weakening
1. `test_daily_multitarget_independent` depends on `samples/daily_plant_multitarget.xlsx`, which is not committed.
   **Contract:** a `conftest.py` fixture runs `samples/generate_samples.py` with a fixed seed inside
   `tmp_path_factory`; the test depends on the **generator**, never on a committed artifact (S6).
2. `test_nn_explicitly_rejected_for_small_data` conflicts with real behaviour, which has two legitimate states
   (`not evaluated` when torch is absent, `rejected` when the data is insufficient).
   **Contract:** make the test **parametric** — torch present ⇒ `rejected`; torch absent ⇒ `not evaluated`.
   The portfolio must record both states in the register.
**Forbidden:** `skip`, `xfail`, deleting the assertion, or changing `core/` logic to make the test pass.

### D0.9 — Seeds for the next phase (no features)
`tests/gui/` with a smoke test that only creates and closes a `QApplication` (`platform=windows` on CI) ·
`tests/reference/README.md` describing the D4 JSON format · `tests/fixtures/synthetic.py` re-exporting the
existing generator for test use only.

### D0.10 — Evidence bundle
`artifacts/phase0/`: `REPORT.md` · `pytest.txt` (raw, both torch-present and torch-absent runs) · `gates.txt`
(all three gates + their negative tests) · `matrix_check.txt` · `deps.txt` (`pip freeze`, lock SHA-256,
`pip-audit`) · `sbom.json` · `git_clean.txt` · `ci_links.txt` (URLs of the green CI runs).

---

## 5. Permitted changes to product code (everything else in `energybaseline/` is frozen)

1. `energybaseline/core/report.py` — **only** remove the WeasyPrint branch, keep the existing fallback path;
   note in `deviations.md`: “interim renderer; the ReportLab renderer lands in Phase 8”.
2. `energybaseline/config/*` — **only** packaging metadata if required.
3. Existing tests — **only** within the boundaries of D0.8.

`launcher.py`, `db/`, `core/pipeline.py`, `core/models.py` and everything else stay untouched (Phase 1).

---

## 6. End-of-phase report format (`artifacts/phase0/REPORT.md` + chat summary)

1. Full commit SHA + `git diff --stat`.
2. Table: `ID` (D0.x plus every AC/requirement covered) → file/function → test.
3. Commands executed + raw output paths.
4. `pytest -q` summary + measured coverage of `core/` (a number, not an adjective).
5. Artifacts + SHA-256 of each.
6. Windows evidence: link to the green `windows-latest` run.
7. Deltas in `deviations` / `assumptions` / `not_implemented`.
8. What was **not** proven, and why.
9. Open risks and their effect on Phase 1.

---

## 7. Acceptance gate G0 (canonical block — must be byte-identical with `docs/PHASE0_BRIEF.md`)

Every command must exit 0. The `test -z "$(…)"` lines assert empty output on purpose: a bare `grep`
exits 1 on a clean tree, which would contradict the gate.

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
test -z "$(git ls-files | grep -Ei 'package.json|tsconfig|\.tsx?$|next\.config|drizzle|eslint|postcss')"
test -z "$(git ls-files | grep -E '__pycache__|\.pyc$')"
test -z "$(grep -rn 'weasyprint' energybaseline scripts requirements.txt requirements.in requirements.lock)"
```

## 8. Auto-fail conditions (no discussion)

- A gate without a negative test.
- A matrix row with `status: done` and empty `test_ids`.
- Resolving a test with `skip`, `xfail`, or a deleted assertion.
- Any remaining `weasyprint` reference in code or manifests.
- Any remaining Next/React file tracked by git.
- A lock file without hashes, or with `>=` range pins.
- A mock-based test standing in for a `[M]` requirement.
- Feature work outside this phase's scope.
- Claiming “green” without raw command output.
- Modifying `LICENSE` or deleting `artifacts/review_baseline/`.

---

## 9. Closing

Deliver the report and **stop**. If something is genuinely impossible in your environment (no
`windows-latest`, no torch, no network), say so explicitly and leave evidence — concealment costs more
than a defect. Then wait for the reviewer's vote; Phase 1 (Lifecycle & Skeleton) stays closed until
Phase 0 passes.
