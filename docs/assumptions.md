# Standing Engineering Assumptions (`S1`–`S7`)

Last updated: `2026-11-12` (Ratified in `docs/DECISIONS.md`)

These seven standing assumptions govern EnergyBaseline v6 across Phases 0–9 unless explicitly superseded by a written entry in `docs/DECISIONS.md`.

| ID | Assumption | Enforcement / Anchor |
|---|---|---|
| **S1** | **Target Python runtime is `3.11.9` (`>=3.11,<3.12`).** Every scientific and desktop dependency (`PySide6`, `numpy`, `scipy`, `statsmodels`, `scikit-learn`, `xgboost`, `lightgbm`, `catboost`, `optuna`, `shap`, `mapie`, `arch`, `reportlab`, `torch`) ships prebuilt `win_amd64` and `manylinux_x86_64` wheels for CPython 3.11. | `pyproject.toml` (`requires-python = ">=3.11,<3.12"`), `.github/workflows/ci.yml` (`python-version: "3.11.9"`), `docs/SUPPORT_MATRIX.md`. |
| **S2** | **Git history is preserved; cleanup uses forward commits only.** Legacy web-stack files and bytecode are removed via `git rm` in linear commits without `git filter-repo` or force-pushes. | Linear branch history + `scripts/gates/guard_web_free.py`. |
| **S3** | **Multiprocessing uses `spawn` everywhere (`mp_context="spawn"`).** Windows only supports `spawn`; Linux CI and developer runs must also use `spawn` in Phase 1+ so serialization boundaries and process startup behave identically across OSes. | Recorded here for Phase 1 `ProcessPoolExecutor` implementation (`IPC-1..7`). |
| **S4** | **Code style and static checks are enforced by `ruff` (`ruff check .` + `ruff format --check .`) plus `pyflakes energybaseline`.** No duplicate formatters (`black`, `isort`, `flake8`) are added. | `pyproject.toml` (`[tool.ruff]`), `.github/workflows/ci.yml`, Gate `G0`. |
| **S5** | **Minimum line coverage gate on `energybaseline/core` ratchets by phase:** **60%** at Gate `G0` (baseline reality ~66%) and **80%** from Phase 2 (`G2`) through Phase 9 (`G9`). | `.github/workflows/ci.yml` (`--cov=energybaseline/core --cov-fail-under=60`). |
| **S6** | **Sample binary `.xlsx` datasets are generated deterministically, never committed to Git.** `samples/generate_samples.py` (`seed=2024`) generates `daily_plant_multitarget.xlsx` during install (`install.bat`) and in temporary pytest session directories (`tests/conftest.py::generated_samples_dir`). | `.gitignore` (`samples/*.xlsx`), `tests/fixtures/synthetic.py`, `tests/conftest.py`. |
| **S7** | **Repository license remains `Proprietary / Internal` (`LICENSE` untouched).** All third-party runtime dependencies must be permissive (`MIT`, `BSD`, `Apache-2.0`, `PSF`) or dynamically linked `LGPL-3.0` (`PySide6`); zero `GPL` viral runtime libraries are permitted. | `LICENSE` (unmodified), `THIRD_PARTY_NOTICES.md`, `scripts/gates/guard_web_free.py`. |
