# Python 3.12 migration and main integration

## Authorized scope

Integrate `origin/main` (`fed56d58d18c20e0a689b5f510ed2e7c357034d9`) into `arena/5b0e8eef-energybaseline`, retain the remediation/security contracts, migrate the complete runtime policy to Python 3.12, regenerate dependency manifests, and execute fresh Windows/Linux CI. Do not change main, merge/close PRs, remove tests or weaken coverage.

## Implementation

- `.python-version` declares `3.12`. Both Actions workflows consume that file and resolve the latest published patch available for their runner. A single exact patch is not assumed to exist on both Windows and Ubuntu 26.04.
- `pyproject.toml` requires `>=3.12,<3.13`; Ruff targets `py312`. Installation rejects an existing non-3.12 virtualenv. Both workflows verify the actual interpreter and run `pip check` before tests.
- Direct pins from main were resolved with uv 0.13.8 using `--python-version 3.12 --universal --generate-hashes`. The exact manifest and hashed lock share 164 package pins. `requirements-dev.txt` continues to reference the exact runtime manifest and retain all original gates/tools.
- PySide6 6.12's metapackage newly includes PySide6-WebEngine/Pdf. Native `PySide6-Essentials==6.12.0` and `PySide6-Addons==6.12.0` retain Qt widgets/charts/SVG without introducing a browser dependency. This is asserted in tests and the existing web-free gate remains enforced.
- Keep main's window/icon/theme and statistical catalog improvements. Preserve explicitly selected targets across dataset reloads without enabling energy-name heuristics outside the energy domain. Derive schema target roles through ColumnSpec rather than assigning to the read-only `DatasetSchema.target_columns` property. New GUI regression covers both neutral and energy domains, stale role metadata, collect/reload, and maximizable setup windows.
- Do not adopt main's synthetic daily fallback when no timestamp is selected. It bypassed existing schema/temporal validation and invented granularity. Preserve the established temporal implementation and explicit timestamp requirement, with all existing temporal tests unchanged.
- Existing workflow contract tests now assert the authorized 3.12 policy; a new semantic contract checks shared version declarations, excluded unsupported minors, manifest equality and preflight ordering. No tests were removed or skipped.

## Evidence status

The historical 493-pass Python 3.11 review and CI run 43691420952 are not Python 3.12 evidence.

Initial integration preflight on the existing Python 3.11 environment: 11 runtime/workflow structure tests passed; 15 existing GUI tests passed in 45.23 seconds; two new integration GUI cases passed. These are compatibility preflights, not migration acceptance. Ruff lint/format, pyflakes, compileall, web-free guard, baseline-test integrity and requirements-matrix checks passed.

The sandbox's prebuilt Python 3.12 download is blocked by the outbound release-asset host restriction. A local CPython 3.12.16 source build is being attempted with explicit native development headers/libraries. Native 3.12 execution, full tests and fresh two-OS CI results will be recorded below when available. No completion claim is made from the preflight alone.

## Reproduction

```sh
python -m pip install uv==0.13.8
uv pip compile requirements.in --python-version 3.12 --universal --generate-hashes --output-file /tmp/requirements312.lock
# The committed lock removes resolver annotations, not package pins or hashes.
python -m pip install --require-hashes -r requirements.lock
python -m pip install -r requirements-dev.txt
python -m pip install --no-build-isolation --no-deps -e .
python scripts/verify_runtime.py
python -m pip check
python -m pytest tests/ --cov=energybaseline/core --cov-fail-under=60
```

Linux GUI execution additionally requires the native Qt/ODBC/SecretService packages declared in CI. Native credential and ODBC evidence must come from actual OS services, not mocked substitutes. Packaging/production signing and real remote database TLS retain their existing certification limits.
