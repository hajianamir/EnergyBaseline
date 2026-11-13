# PHASE 0 BRIEF — Truth & Rails (mandatory work order)

**Preconditions:** the decision bundle is RATIFIED (`docs/DECISIONS.md`): D1=B, D2=B+A, D3=A, D4=B, D5=A, S7=Proprietary.
**Goal:** turn the repository from a prototype into an auditable workshop. **No product feature may be added in
this phase** — no VSG, no TCP, no test catalog, no ProcessPool, no new report, no new UI. Anything outside this
brief is rejected.

**Scope:** G-1, G-2, G-7, §27, AC-21, AC-22, PKG-6 + the consequences of D1..D5 + S1..S6
**Estimate:** 1–1.5 engineer-weeks · **Deliverable:** `artifacts/phase0/REPORT.md` (§4.2 format) + a push on the branch
**Companion prompt (copy-ready):** `docs/PROMPT_PHASE0_EXECUTOR.md`

---

## 0. Absolute prohibitions in this phase

1. Do not touch anything under `energybaseline/core/` for “improvement”. Only two changes are permitted:
   (a) `launcher.py` to remove the `DATABASE_URL` override, (b) `core/report.py` to remove the WeasyPrint branch.
2. Do not delete or weaken any existing test. The two broken tests are resolved by an explicit documented
   contract (§8), never by `skip`/`xfail`/deleted assertions.
3. Do not add dependencies beyond the list in §2.
4. Never touch `main` and never push to the reviewer's branch. Your work goes on
   `arena/3611a52f-energybaseline`, branched from the reviewer's tip `abd0eae` (see §1.1 of the prompt).

---

## 1. Remove the web stack (G-1/G-2/AC-01/AC-35)

```bash
git rm -r package.json package-lock.json next.config.ts next-env.d.ts tsconfig.json \
         postcss.config.mjs eslint.config.mjs drizzle.config.json src/
```
- `README.md`: remove every Next/Tailwind/drizzle reference; add “Python-only, PySide6, offline”.
- `install.bat` / `start.bat` / `stop.bat`: remove every npm/node invocation.
- **Proof:** `git ls-files | grep -Ei 'package.json|tsconfig|\.tsx?$|next\.config|drizzle|eslint|postcss'`
  must be empty, and `guard_web_free.py` must pass.

---

## 2. Dependencies: pins, lock, remove WeasyPrint (D5, S1, PKG-6)

1. `requirements.in` (input constraints) → `requirements.txt` with **exact `==` pins** for everything, including
   the currently missing: `joblib`, `mapie==1.6.0`, `arch==9.0.0`, `sqlglot==30.31.0`, `reportlab==5.1.0`,
   `pydantic` (pinned), `hypothesis`, `pytest-qt`, `ruff`, `cyclonedx-bom`, `pip-audit`, and `pywin32`
   behind a Windows marker.
2. `requirements.lock` generated with `--generate-hashes` (pip-tools or `uv pip compile --generate-hashes`).
3. **Remove `weasyprint`** from `requirements.txt`, `energybaseline/core/report.py`, `scripts/build_exe.py`,
   `README.md`, `docs/USER_GUIDE.md`.
4. Target Python: **3.11.9** (`python_requires = ">=3.11,<3.12"`, recorded in `docs/SUPPORT_MATRIX.md`).
5. **Proof:** the lock installs with `--require-hashes`; `grep -rn weasyprint` over code and manifests is empty;
   `python -c "import reportlab, mapie, arch, sqlglot"` succeeds.

---

## 3. Machine-checkable gates (these are the reviewer's instruments, not decoration)

Build three scripts and a **negative test for each** (the test must prove the gate actually fails):

| Script | Measures | Mandatory negative test |
|---|---|---|
| `scripts/gates/guard_web_free.py` | **AST scan** of every `*.py` for banned imports (`weasyprint`, `PySide6.QtWebEngineWidgets`, `flask`, `fastapi`, `uvicorn`, `http.server`) + manifest scan + rejection of web-stack files in `git ls-files` | `tests/gates/test_guard_web_free.py` creates a temp file with `import weasyprint` and expects exit ≠ 0 |
| `scripts/gates/guard_env_readers.py` | Only `config/settings.py` may read DB environment variables | temp file with `os.environ["DATABASE_URL"]` ⇒ exit ≠ 0 |
| `scripts/gates/guard_tests_integrity.py` | SHA-256 of baseline tests in `tests/.baseline.sha256` | flip one byte of a test ⇒ exit ≠ 0 |

**Documented exemption:** `docs/EXECUTION_PLAN.md`, `docs/DECISIONS.md`, `docs/PHASE0_BRIEF.md`,
`docs/PROMPT_PHASE0_EXECUTOR.md` and `artifacts/**` only quote the words and are not scanned. State this in the
script docstring, not only in the report — a document is not a dependency.

---

## 4. Mandatory CI (D2, PKG-6, §27)

`.github/workflows/ci.yml`:
```yaml
strategy: { matrix: { os: [windows-latest, ubuntu-latest] } }
steps:
  - checkout
  - setup-python 3.11.9
  - pip install -r requirements.lock --require-hashes       # Windows: full; Linux: without pywin32
  - ruff check . && ruff format --check .
  - python -m pyflakes energybaseline
  - python scripts/gates/guard_web_free.py
  - python scripts/gates/guard_env_readers.py
  - python scripts/gates/guard_tests_integrity.py
  - python -m pytest -q --cov=energybaseline/core --cov-fail-under=60      # S5
  - python -m pytest -q tests/gui -p no:cacheprovider                      # Windows: platform=windows
  - pip-audit -r requirements.lock
  - cyclonedx-py requirements -o sbom.json
  - upload-artifact: sbom.json, coverage.xml, artifacts/**
```
- G0 does not accept a Linux-only CI: the two-OS matrix is mandatory.
- `.github/workflows/r-reference.yml`: nightly + `workflow_dispatch` (D4) — **skeleton only** in this phase,
  plus `tests/reference/README.md`.

---

## 5. Generated requirements matrix (AC-22, traceability)

`scripts/gen_matrix.py`:
- The source of IDs is a **data file**, `docs/requirements.yaml`, covering AC-01..AC-35 and the families
  DAT-1..8, VSG-1..9, TCP-1..14, T1..T14, UQ-1..5, PM-1..7, DR-1..5, PER-1..6, REP-1..5, RPT-1..7, UX-1..12,
  SHL-1..9, DB-1..11, LIF-1..5, SCR-1..7, AUTO-1..6, MT-1..5, VAL-1..7, SC-1..10, CI-1..4, DOM-1..3, PKG-1..6.
- Each row: `id, priority(M/S/O), phase, code_refs[], test_ids[], status`.
- `--check` must enforce: every ID present · no `status: done` with empty `test_ids` · no AC without a phase.
- Output `docs/requirements_matrix.md` with a “generated — do not edit” header.

---

## 6. Process documents (English, per G-7)

| File | Content | Initial state |
|---|---|---|
| `docs/deviations.md` | the 10 baseline gaps + the library deviations (§2 of the plan) + **PKG-3 → [S] with its trigger** (D1) | all OPEN |
| `docs/assumptions.md` | S1..S7 + silence defaults | ADOPTED (objection window until G0) |
| `docs/not_implemented.md` | TFT/PatchTST/Informer, CatBoost-when-absent, … | initial list |
| `SECURITY.md` | initial threat model (local attacker, malicious data file, SQL injection, artifact tampering) | draft |
| `THIRD_PARTY_NOTICES.md` | licenses | draft |
| `docs/SUPPORT_MATRIX.md` | Python 3.11.9 · Win10 22H2/Win11 · **D2 reference hardware** · pinned CPU torch wheel | final |
| `CHANGELOG.md` | first entry | draft |

---

## 7. Repository hygiene

`.gitignore` must cover `data/`, `logs/`, `artifacts/**/*.db|sqlite|joblib`, `*.joblib`, `*.ebproj`, `build/`,
`dist/`, `_internal/`, `EnergyBaseline.exe`, `*.spec`, `__pycache__/`, `.venv/`.
Check: `git ls-files | grep -E "\.pyc$|__pycache__"` is empty.

---

## 8. The two broken tests — by contract, not by weakening

**8.1 `test_daily_multitarget_independent` (depends on an uncommitted file)**
- **Contract:** `samples/*.xlsx` stays out of git; a `conftest.py` fixture runs `samples/generate_samples.py`
  with a fixed seed inside `tmp_path_factory` (session scope). The test depends on the **generator**, not an artifact (S6).

**8.2 `test_nn_explicitly_rejected_for_small_data` (behaviour vs assertion)**
- **New explicit contract:** the portfolio must record **both** states in its register:
  `not evaluated` (prerequisite missing, e.g. torch absent) and `rejected` (insufficient data).
  The test becomes **parametric**: torch present ⇒ `rejected`; torch absent ⇒ `not evaluated`.
- **Forbidden:** deleting the `all(...)` condition without a replacement; skipping the test.

**Proof:** `pytest -q` on a clean machine (no torch) and on a machine with torch, both documented in the report.

---

## 9. Seeds for future phases (no features)

- `tests/gui/` containing one smoke test that creates and closes a `QApplication` (`platform=windows` on CI).
- `tests/reference/README.md` with the D4 JSON format — no statistical tests yet.
- `tests/fixtures/synthetic.py` — only a re-export of the existing generator for test use.

---

## Gate G0 — canonical block (single source of truth: this section)

Every command below must exit 0. The block is byte-identical in `docs/PROMPT_PHASE0_EXECUTOR.md` §7 and
referenced by `docs/EXECUTION_PLAN.md`; if one copy changes, all three change in the same commit.
The three `test -z "$(…)"` lines assert **empty output**; a bare `grep` would exit 1 on a clean tree and
would therefore contradict “must exit 0”.

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

On Windows (CI): `ci.yml` green; the nightly R job remains a skeleton.

**Note on the empty-output greps:** a clean result is *no output and exit code 1* from `grep`. The gate
requires “no matches”, so the executor must capture output and assert emptiness explicitly
(`test -z "$(...)"`) rather than relying on grep's exit status, which is inverted for that purpose.

---

## Gate G1 — canonical block (byte-identical with `docs/EXECUTION_PLAN.md` §4)

Every command below must exit 0. The block is byte-identical in `docs/EXECUTION_PLAN.md` §4.6 and
`docs/PROMPT_PHASE1_EXECUTOR.md` §7; if one copy changes, all copies change in the same commit.

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

## Expected evidence bundle (`artifacts/phase0/`)

| File | Content |
|---|---|
| `REPORT.md` | the 9-section report of the plan |
| `pytest.txt` | raw `pytest -q` output (torch present and absent) |
| `gates.txt` | output of all three gates plus their negative tests |
| `matrix_check.txt` | `gen_matrix.py --check` output and ID counts |
| `deps.txt` | `pip freeze`, lock SHA-256, `pip-audit` output |
| `sbom.json` | CycloneDX |
| `git_clean.txt` | the cleanup greps of §1 and §7 |

---

## Instant rejection criteria (no discussion)

- A gate without a negative test.
- A matrix row `done` with empty `test_ids`.
- Resolving a broken test with `xfail`/`skip`/deleted assertion.
- Any remaining `weasyprint` reference in code or manifests.
- Any remaining Next/React file tracked by git.
- A lock without hashes or with `>=`.
