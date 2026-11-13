# Phase 0 Executor Report — Truth & Rails (`G0`)

- **Date:** `2026-11-12`
- **Executor Branch:** `arena/3611a52f-energybaseline` (sole push target; `main` and `arena/af928d3c-energybaseline` untouched)
- **Reviewer Governance Base Commit Merged (`--ff-only`):** `3facd158443615a78d69fde9f3f5a3a014e03dc8` (parent `abd0eae68b826477731596fa932988a0e011cdf4`, sitting on `origin/main` `2c92f2a06f6fb04cd3aed584ba82639144bfbfa1`)
- **Language Compliance (`G-7`):** 100% English across all code, docstrings, comments, tests, workflows, process documents, commit messages, and reports.
- **License Compliance (`S7`):** `LICENSE` untouched (`sha256: 1c633e61e328b34b883571ae25a92cf6a390e6f2752871331545d70a5b553afe`).
- **Baseline Audit Preservation:** `artifacts/review_baseline/` and `artifacts/phase0_ready/` untouched.

---

## 1. Deliverables Summary (`D0.1`–`D0.10`)

| ID | Deliverable | Key Files | Verification / Result |
|---|---|---|---|
| **D0.1** | Web-stack & web-word removal | Removed 12 tracked files (`package.json`, `tsconfig.json`, `next.config.ts`, `postcss.config.mjs`, `eslint.config.mjs`, `drizzle.config.json`, `src/**`); cleaned `README.md`, `docs/USER_GUIDE.md`, `install.bat` | `guard_web_free.py` exit `0`; `git ls-files` web grep empty |
| **D0.2** | Pinned manifests, hashed lockfile & WeasyPrint removal | `pyproject.toml`, `requirements.in`, `requirements.txt`, `requirements-dev.txt`, `requirements.lock`, `docs/SUPPORT_MATRIX.md`, `energybaseline/core/report.py`, `scripts/build_exe.py` | Zero `>=`/`~=` range pins; 115 packages with SHA-256 wheel hashes; `pip install --require-hashes` & `pip-audit` exit `0` on Windows & Linux |
| **D0.3** | Automated architecture & test-integrity gates + negative tests | `scripts/gates/guard_web_free.py`, `scripts/gates/guard_env_readers.py`, `scripts/gates/guard_tests_integrity.py`, `tests/.baseline.sha256`, `tests/gates/*` | All 3 gates exit `0` on repo; 21 positive + negative gate tests pass in `tests/gates` |
| **D0.4** | Two-OS CI pipeline + R reference nightly skeleton | `.github/workflows/ci.yml`, `.github/workflows/r-reference.yml`, `scripts/ci_junit_annotations.py` | Green on **both** `windows-latest` (job `131526003709`) and `ubuntu-latest` (job `131526003259`) in Actions run `43316565422` |
| **D0.5** | Generated requirements traceability matrix | `docs/requirements.yaml` (249 IDs), `scripts/gen_matrix.py`, `docs/requirements_matrix.md` | `python scripts/gen_matrix.py --check` exit `0`; negative tests verify rejection of `done` without `test_ids`, missing IDs, and phaseless `AC-*` |
| **D0.6** | Process, security, third-party & signing documentation | `docs/deviations.md`, `docs/assumptions.md`, `docs/not_implemented.md`, `docs/SUPPORT_MATRIX.md`, `SECURITY.md`, `THIRD_PARTY_NOTICES.md`, `CHANGELOG.md`, `scripts/sign/sign_artifacts.ps1` | Verified by `tests/gates/test_phase0_contracts.py` and Windows CI `sign_artifacts.ps1 -SelfTest` (`signtool sign` -> `signtool verify /pa`) |
| **D0.7** | Repository hygiene & line-ending determinism | `.gitignore`, `.gitattributes` | Zero tracked `__pycache__`, `*.pyc`, or `samples/*.xlsx`; cross-OS LF byte identity verified by `guard_tests_integrity.py` on `windows-latest` |
| **D0.8** | Baseline test suite green without weakening assertions | `tests/conftest.py`, `tests/test_pipeline.py`, `alembic/env.py` (`test_core.py` & `test_config_db.py` byte-identical) | 42 passed, 0 failed, 0 skipped, 0 xfailed (Full `.venv` **and** torch-absent `/tmp/venv_notorch`) |
| **D0.9** | GUI smoke test, R reference JSON spec & synthetic fixture | `tests/gui/test_qapp_smoke.py`, `tests/reference/README.md`, `tests/fixtures/synthetic.py` | `pytest -q tests/gui` passes on `windows-latest` (`platform == "windows"`) and `ubuntu-latest` (`offscreen`) |
| **D0.10** | Verification evidence bundle & SBOM | `artifacts/phase0/{REPORT.md,pytest.txt,gates.txt,matrix_check.txt,deps.txt,sbom.json,git_clean.txt,ci_links.txt}` | Raw outputs + SHA-256 digests recorded below |

---

## 2. Canonical Gate `G0` Results

Every command in `docs/PHASE0_BRIEF.md` §1 exited `0`:

| # | Gate Command | Exit Code | Result Summary |
|---|---|---|---|
| 1 | `python -m pytest -q` | `0` | `42 passed` (Full `.venv`: 77.60 s; torch-absent venv: 45.25 s) |
| 2 | `python -m pytest -q tests/gates` | `0` | `21 passed` (positive + negative tests across all gates) |
| 3 | `python -m ruff check . && python -m ruff format --check .` | `0` | `All checks passed! 32 files already formatted` |
| 4 | `python -m pyflakes energybaseline` | `0` | Zero warnings |
| 5 | `python scripts/gates/guard_web_free.py` | `0` | `OK [guard_web_free] clean` |
| 6 | `python scripts/gates/guard_env_readers.py` | `0` | `OK [guard_env_readers] clean` |
| 7 | `python scripts/gates/guard_tests_integrity.py` | `0` | `OK [guard_tests_integrity] verified 4 baseline test files` |
| 8 | `python scripts/gen_matrix.py --check` | `0` | `OK [gen_matrix] verified 249 requirement IDs (10 done)` |
| 9 | `python -m pytest -q --cov=energybaseline/core --cov-fail-under=60` | `0` | **`79.82%` total line coverage** (`3340` stmts, `674` miss; threshold `60%`) |
| 10 | `test -z "$(git ls-files \| grep -Ei 'package.json\|tsconfig\|\\.tsx?$\|next\\.config\|drizzle\|eslint\|postcss')"` | `0` | Empty |
| 11 | `test -z "$(git ls-files \| grep -E '__pycache__\|\\.pyc$')"` | `0` | Empty |
| 12 | `test -z "$(grep -rn 'weasyprint' energybaseline scripts requirements.txt requirements.in requirements.lock)"` | `0` | Empty |

---

## 3. Live GitHub Actions CI Evidence (`D0.4`)

- **Workflow Run:** `https://github.com/hajianamir/EnergyBaseline/actions/runs/43316565422` (`status: completed`, `conclusion: success`, commit `d82c112389d923c63f59bee459446190a5b63338`)
  - **`Phase 0 Gate (windows-latest)`:** `https://github.com/hajianamir/EnergyBaseline/actions/runs/43316565422/job/131526003709` — **`conclusion: success`** (Python `3.11.9`, `--require-hashes`, `ruff`, `pyflakes`, all 4 gates, `pytest --cov=energybaseline/core --cov-fail-under=60`, `pytest -q tests/gui` with `platform == "windows"`, `sign_artifacts.ps1 -SelfTest` with `signtool sign` + `signtool verify /pa`, `pip-audit`, `cyclonedx-py` SBOM, artifact `ci-artifacts-windows-latest` `sha256:12e512bca9ad1fb604f65464a595da541ea479e3ccc595fc837fa1ce72d7389d`).
  - **`Phase 0 Gate (ubuntu-latest)`:** `https://github.com/hajianamir/EnergyBaseline/actions/runs/43316565422/job/131526003259` — **`conclusion: success`** (Ubuntu 26.04 Python `3.11.18`, `--require-hashes`, `ruff`, `pyflakes`, all 4 gates, `pytest --cov`, headless `pytest -q tests/gui`, `pip-audit`, `cyclonedx-py` SBOM, artifact `ci-artifacts-ubuntu-latest` `sha256:38cd1639b9c2cd8aea6f5bb0a40961d1e3ae5a44ddf676c0fc4e7354e457c5a4`).
- **R Reference Nightly Skeleton (`.github/workflows/r-reference.yml`):** Committed with `schedule` (`0 3 * * *`) and `workflow_dispatch`; GitHub activates `workflow_dispatch` / cron registration once the workflow lands on the default branch after reviewer approval.

---

## 4. SHA-256 Manifest of Key Phase 0 Artifacts

| File | SHA-256 |
|---|---|
| `LICENSE` | `1c633e61e328b34b883571ae25a92cf6a390e6f2752871331545d70a5b553afe` |
| `pyproject.toml` | `54039168cf44b86340399433902850960e062f18b3457c7e43d1c62fc130d2e0` |
| `requirements.in` | `f951f98ab21d4cc7cb6ea9977a0dc4b39cdad6bd4c7d5b36b65ef2fdd97eea9d` |
| `requirements.txt` | `4e592401169ec8b4bd608d5d65c4aaaead729dc86678ab1b83e1196e53ad687e` |
| `requirements-dev.txt` | `69075054c6d3bf5e0104bbc5d72895f380f050216a76ee8978dc4fbfd62f95e8` |
| `requirements.lock` | `de38e74b2bc62ccdbfa41eb0721126c6190ed91a15ebec54cb5dfac133fe4ca8` |
| `tests/.baseline.sha256` | `6f3abde78940ac71c104a32305c7a69bc2a9c90a09f6660a332de0eac467f3dc` |
| `docs/requirements.yaml` | `00f36d69b2f501a99115750b5dc947f6e59bd1fa43604798ce9fcc8ab85d8eff` |
| `docs/requirements_matrix.md` | `a66379fde4ad837d71ee321499a4d9ee46d4ba7242a28139af5d129ecb00b592` |
| `artifacts/phase0/pytest.txt` | `c2bcf20cb15ea36df5bde735ca6550abf5cb8600ce50c5b545142d43b33138c5` |
| `artifacts/phase0/gates.txt` | `6861fb81cfe6b3c818e3e9f5c0a1926ed30612ce8788ddedd98db31e6a8ffecd` |
| `artifacts/phase0/matrix_check.txt` | `65da7f986af62b7e040c4857fb74f44363cb2db6d796acc235feafa4fa2ed2b1` |
| `artifacts/phase0/deps.txt` | `418f605a3e8fa11f192c2a4f38240adc0766bbe5be1aa64c1c132253f16e13fc` |
| `artifacts/phase0/sbom.json` | `c6ef9b1550c90a68bd6cd94899b40312e0100ed5ab662a219e1596bdc3c67ab8` |
| `artifacts/phase0/ci_links.txt` | `c86a5a9140023dea278d6b89919b96741fb02494a2918195f5cd4da1b5327f01` |

---

## 5. Recorded Deviations & Technical Notes (`docs/deviations.md`)

1. **`DEV-01` (`D1 = B`, `PKG-3` `[M] -> [S]`):** Authenticode hardware token deferred for internal developer builds; `scripts/sign/sign_artifacts.ps1 -SelfTest` verified live on `windows-latest`; mandatory upgrade trigger recorded before **any** distribution outside developer machines.
2. **`DEV-02` (`D5 = A` interim PDF path):** Legacy HTML-to-PDF GTK path removed in `energybaseline/core/report.py` and banned by `guard_web_free.py`; existing Qt/matplotlib PDF fallback remains active until Phase 8 lands the full ISO 50006 ReportLab Platypus renderer.
3. **`DEV-03` (Isolated zero-logic `pyflakes` hygiene):** Isolated in commit `ff1daec` (`style(phase0): clear baseline pyflakes unused-symbol warnings with zero logic change`) so Gate `G0` (`python -m pyflakes energybaseline`) exits `0` without weakening the gate.
4. **`DEV-04` (`alembic/env.py` `NullPool` + `engine.dispose()` on Windows):** Under `SQLAlchemy==2.1.8`, file-backed SQLite defaults to `QueuePool`, leaving a pooled handle open in `alembic/env.py` that triggered Windows `[WinError 32]` when `tests/test_config_db.py::test_alembic_upgrade_on_clean_machine` unlinked `app.db`. Fixed in `alembic/env.py` (`NullPool` + `finally: engine.dispose()`) with zero changes to `tests/test_config_db.py`. Also pinned `3.11.18` on `ubuntu-latest` (migrated by GitHub to Ubuntu 26.04) while keeping exact `3.11.9` on `windows-latest`.
5. **Library API findings for Phase 4:** `MICEData.mcar_test()` absent in `statsmodels==0.15.0`; `acorr_ljungbox` in `statsmodels.stats.diagnostic`; `grangercausalitytests(verbose=...)` removed; `adfuller`/`kpss`/`acorr_lm` `result_object` `FutureWarning`; `scipy.stats.brunnermunzel(midrank=...)` deprecated in `scipy==1.17.1`.

---

## 6. Open Questions for Reviewer

None. Phase 0 deliverables `D0.1`–`D0.10` and Gate `G0` are complete on `arena/3611a52f-energybaseline`. Executor is stopped awaiting Reviewer sign-off before Phase 1.
