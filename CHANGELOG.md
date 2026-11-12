# Changelog

All notable changes to EnergyBaseline are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased] — Phase 0: Truth & Rails (2026-11-12)

### Added
- Exact-pinned dependency manifests (`requirements.in`, `requirements.txt`, `requirements-dev.txt`) and cross-platform SHA-256 hashed lockfile (`requirements.lock`) targeting Python `3.11.9` (`pyproject.toml`), including `reportlab==5.1.0`, `mapie==1.6.0`, `arch==9.0.0`, `sqlglot==30.31.0`, `joblib==1.6.0`, `pydantic==2.14.3`, `hypothesis==6.170.15`, `pytest-qt==4.5.0`, `ruff==0.17.5`, `cyclonedx-bom==8.0.1`, and `pip-audit==2.10.1` (`D0.2`).
- Automated architecture and integrity gates (`scripts/gates/guard_web_free.py`, `scripts/gates/guard_env_readers.py`, `scripts/gates/guard_tests_integrity.py` + `tests/.baseline.sha256`) and positive/negative gate tests in `tests/gates/` (`D0.3`).
- GitHub Actions CI workflow (`.github/workflows/ci.yml`) on `[windows-latest, ubuntu-latest]` running hashed lockfile install, `ruff`, `pyflakes`, architecture gates, `pytest --cov=energybaseline/core --cov-fail-under=60`, `pytest-qt` GUI smoke test, Windows self-signed Authenticode signing smoke test, `pip-audit`, and CycloneDX SBOM generation, plus nightly R reference skeleton `.github/workflows/r-reference.yml` (`D0.4`).
- Machine-readable requirements source of truth (`docs/requirements.yaml`), generator/validator (`scripts/gen_matrix.py`), and generated traceability matrix (`docs/requirements_matrix.md`) covering all 225 mandatory specification IDs (`D0.5`).
- Governance and support documentation: `docs/deviations.md`, `docs/assumptions.md`, `docs/not_implemented.md`, `docs/SUPPORT_MATRIX.md`, `SECURITY.md`, `THIRD_PARTY_NOTICES.md`, and `scripts/sign/sign_artifacts.ps1` (`D0.6`).
- Deterministic session sample fixture (`tests/fixtures/synthetic.py`, `tests/conftest.py::generated_samples_dir`), `QApplication` smoke test (`tests/gui/test_qapp_smoke.py`), and R reference JSON format specification (`tests/reference/README.md`) (`D0.8`, `D0.9`).

### Changed
- `install.bat` now installs strictly from `requirements.lock` with `--require-hashes`; `scripts/build_exe.py` exposes `--profile full|lite` per `D3 = A` (`D0.1`, `D0.2`).
- Parametrized `tests/test_pipeline.py::test_nn_explicitly_rejected_for_small_data` over `["MLP", "LSTM"]` to verify exact small-data rejection and torch-absent contracts (`D0.8`).
- Expanded `.gitignore` and added `.gitattributes` to enforce LF line endings on source/test files and prevent generated binaries or caches from entering Git (`D0.7`).

### Removed
- Deleted all vestigial Next.js / TypeScript / Drizzle web-stack files (`package.json`, `tsconfig.json`, `next.config.ts`, `postcss.config.mjs`, `eslint.config.mjs`, `drizzle.config.json`, `src/**`) and scrubbed web/env references from `README.md` and `docs/USER_GUIDE.md` (`D0.1`).
- Removed the GTK HTML-to-PDF code path from `energybaseline/core/report.py` and `scripts/build_exe.py` per ratified decision `D5 = A` (`D0.2`).

## [6.0.0-baseline] — 2026-11-11 (`2c92f2a`)
- Initial EnergyBaseline v6 baseline desktop application import audited prior to Phase 0.
