# Phase 1 Executor Report — Lifecycle & Skeleton (`G1`)

- **Date:** `2026-11-12` (UTC; latest Windows CI run completed `2026-11-13T07:19Z`)
- **Executor Branch:** `arena/3611a52f-energybaseline` (sole push target; `main` and `arena/af928d3c-energybaseline` untouched; linear history, zero force-push)
- **Reviewer Base Commit (`arena/af928d3c-energybaseline`):** `23fab18bd21e5f827d41e0b7c1ff80a59edb2077` (sitting atop `main` `2c92f2a06f6fb04cd3aed584ba82639144bfbfa1`)
- **Language Compliance (`G-7`):** 100% English across all code, docstrings, comments, PowerShell scripts, tests, workflows, commit messages, and reports.
- **License & Frozen Baseline Integrity (`S7`, `D0.3`):** `LICENSE` (`sha256: 1c633e61e328b34b883571ae25a92cf6a390e6f2752871331545d70a5b553afe`), `tests/.baseline.sha256` (`sha256: 6f3abde78940ac71c104a32305c7a69bc2a9c90a09f6660a332de0eac467f3dc`), `artifacts/review_baseline/`, and `artifacts/phase0/` untouched.

---

## 1. Phase ID, Full Commit SHA, and `git diff --stat`

- **Phase ID:** `Phase 1 — Lifecycle & Skeleton (Gate G1)`
- **Code Commit Verified Green on 2-OS CI:** `046eab2230c6429066a0c5bef01fcd4290256efd` (short: `046eab2`, immediately preceding this final `artifacts/phase1/` evidence commit on linear history `23fab18..HEAD`)
- **Permitted File Scope (`PROMPT_PHASE1_EXECUTOR` §5 & `EXECUTION_PLAN.md` §5.3):** Strictly honored — zero edits outside `energybaseline/launcher.py`, `energybaseline/gui/*`, `energybaseline/core/pool.py` (transport adapter only; pipeline/models/nn/mining/store untouched), `energybaseline/db/*`, `energybaseline/config/settings.py` (`energybaseline/config/__init__.py` untouched vs `23fab18`), `energybaseline/io/*`, `scripts/build_exe.py`, `alembic/env.py`, `scripts/gates/guard_no_qthread_compute.py`, `scripts/evidence/*`, `tests/gates/*`, `tests/gui/*`, `.github/workflows/ci.yml`, `CHANGELOG.md`, `docs/requirements.yaml`, and `docs/requirements_matrix.md`.

```text
 .github/workflows/ci.yml                      |   50 +-
 CHANGELOG.md                                  |   18 +-
 alembic/env.py                                |    3 +
 docs/requirements.yaml                        |  722 ++++++++++------
 docs/requirements_matrix.md                   |   70 +-
 energybaseline/config/settings.py             |  117 ++-
 energybaseline/core/pool.py                   | 1115 +++++++++++++++++++++++++
 energybaseline/db/__init__.py                 |   31 +-
 energybaseline/db/backup.py                   |  156 ++++
 energybaseline/db/migrate.py                  |    9 +-
 energybaseline/db/session.py                  |  491 +++++++++--
 energybaseline/gui/app.py                     |  196 ++++-
 energybaseline/gui/commands.py                |   81 ++
 energybaseline/gui/main_window.py             |  213 ++++-
 energybaseline/gui/page_status.py             |  297 ++++++-
 energybaseline/gui/pages_setup.py             |  378 ++++++---
 energybaseline/gui/single_instance.py         |  113 +++
 energybaseline/io/__init__.py                 |   21 +
 energybaseline/io/paths.py                    |  272 ++++++
 energybaseline/launcher.py                    |   50 +-
 scripts/build_exe.py                          |  384 +++++++--
 scripts/evidence/dpi_matrix.ps1               |   99 +++
 scripts/evidence/installer_smoke.ps1          |  146 ++++
 scripts/evidence/lif_orphans.ps1              |  164 ++++
 scripts/evidence/startup_time.ps1             |  170 ++++
 scripts/gates/guard_no_qthread_compute.py     |  182 ++++
 tests/gates/test_phase1_db_ladder.py          |  309 +++++++
 tests/gates/test_phase1_paths_and_commands.py |  197 +++++
 tests/gates/test_phase1_pool_and_lifecycle.py |  172 ++++
 tests/gui/test_phase1_gui.py                  |  101 +++
 30 files changed, 5697 insertions(+), 630 deletions(-)
```

---

## 2. ID → File / Function → Test Table

| Requirement / Work ID | File & Function / Class | Verification Test(s) / Gate(s) |
|---|---|---|
| **P1.1 / `R-1` / `G-3` / `IPC-1`** | `energybaseline/core/pool.py::AnalysisPoolManager`, `run_in_pool`, `compute_max_workers` (`min(CPU // 2, tasks)`, `mp_context="spawn"`); `energybaseline/launcher.py::main` (`multiprocessing.freeze_support()`); `energybaseline/gui/pages_setup.py::AnalysisPage`; `scripts/gates/guard_no_qthread_compute.py` | `tests/gates/test_phase1_pool_and_lifecycle.py::test_compute_max_workers_formula`, `test_run_in_pool_executes_analysis_end_to_end_and_clean_shutdown`; `tests/gates/test_phase1_paths_and_commands.py::test_guard_no_qthread_compute_passes_repo_and_catches_synthetic` |
| **P1.1 / `SHL-5`** | `energybaseline/core/pool.py::_worker_initializer` (`_EB_IN_WORKER=1`); `energybaseline/db/session.py::_assert_not_worker`; UI-thread-only `store.new_run` / `store.save_run` in `AnalysisPage` | `tests/gates/test_phase1_pool_and_lifecycle.py::test_pool_handshake_shl5_worker_db_guard_and_hard_kill` |
| **P1.1 / `IPC-2` / `IPC-6` / `IPC-7`** | `energybaseline/core/pool.py::pack_payload`, `unpack_payload`, `handshake` (`PROTOCOL_VERSION = "1.0"`, `30 s` first / `10 s` warm readiness timeout, `> 1 MiB` `{path, sha256, rows}` under `APP_HOME/cache/`, deterministic temp cleanup) | `tests/gates/test_phase1_pool_and_lifecycle.py::test_payload_over_1mib_uses_cache_file_ref_and_checks_sha256`, `test_pool_handshake_shl5_worker_db_guard_and_hard_kill` |
| **P1.1 / `IPC-3` / `IPC-5`** | `energybaseline/core/pool.py::_drain_queue_loop`, `_dispatch_worker_command` (`mp.Queue` pipe IPC, crash/error isolation) | `tests/gates/test_phase1_pool_and_lifecycle.py::test_run_in_pool_executes_analysis_end_to_end_and_clean_shutdown` |
| **P1.1 / `IPC-4` / `SHL-4`** | `energybaseline/core/pool.py::CancelToken` (stage, fold, test, epoch, trial callbacks), `_worker_inner_cancel_hooks`, `PoolJobHandle._on_grace_expired` (`10 s` hard-kill) | `tests/gates/test_phase1_pool_and_lifecycle.py::test_cancel_token_callbacks_raise_interrupted_error`, `test_pool_handshake_shl5_worker_db_guard_and_hard_kill` |
| **P1.2 / `G-8` / `LIF-1` / `LIF-2` / `LIF-3`** | `energybaseline/gui/app.py::perform_clean_shutdown` (`aboutToQuit` -> cancel -> join `5 s` -> hard stop -> WAL checkpoint -> DB close); `energybaseline/gui/main_window.py::closeEvent`; `energybaseline/core/pool.py::WindowsJobObject` (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`) | `tests/gates/test_phase1_pool_and_lifecycle.py::test_pool_handshake_shl5_worker_db_guard_and_hard_kill`; Windows CI `scripts/evidence/lif_orphans.ps1` |
| **P1.3 / `AC-02` / `SHL-1`** | `energybaseline/gui/single_instance.py::SingleInstanceGuard` (`QLocalServer` / `QLocalSocket` primary activation + secondary exit `0`) | `tests/gui/test_phase1_gui.py::test_single_instance_guard_activates_primary_and_rejects_second` |
| **P1.4 / `SHL-3` / `SHL-4` (`AC-04`)** | `energybaseline/io/paths.py::validate_path`, `resolve_app_home`, `resolve_picked_path`, `resolve_temp_dir`, `resolve_cache_dir`; `energybaseline/gui/pages_setup.py::SetupPage.open_file` | `tests/gates/test_phase1_paths_and_commands.py::test_validate_path_accepts_inside_allowed_root`, `test_validate_path_rejects_traversal_and_unc`, `test_validate_path_rejects_outside_allowed_roots`, `test_validate_path_rejects_leaf_and_intermediate_symlinks`, `test_resolve_app_home_precedence`; `tests/gui/test_phase1_gui.py::test_main_window_commands_path_guard_status_page_and_banners` |
| **P1.5 / `SCR-7`** | `energybaseline/gui/commands.py::STATIC_UI_COMMAND_SPECS`, `CommandRegistry`; `energybaseline/core/pool.py::WORKER_COMMAND_REGISTRY`; startup validation in `MainWindow.__init__` | `tests/gates/test_phase1_paths_and_commands.py::test_command_registries_are_compile_time_immutable_and_reject_unknown`, `test_no_eval_exec_or_getattr_in_command_dispatchers` |
| **P1.6 / `AC-01` / `AC-05` / `DB-1` / `DB-2` / `DB-3` / `DB-11`** | `energybaseline/config/settings.py::resolve_database_url`; `energybaseline/db/session.py::get_engine`, `query_sqlite_pragmas`, `self_test`, `init_database` (`%LOCALAPPDATA%` / `portable.flag` -> `%TEMP%` `"data may be lost"` -> `:memory:` red banner; 3 s external probe + audit; full SQLite PRAGMAs; Alembic-only file-backed startup); `energybaseline/launcher.py` (`DATABASE_URL` override removed) | `tests/gates/test_phase1_db_ladder.py::test_launcher_does_not_override_database_url`, `test_db_matrix_no_env_and_whitespace_env_and_pragmas`, `test_db_matrix_unreachable_postgres_probe_audits_and_warns`, `test_db_matrix_unwritable_app_home_falls_back_to_temp_rung`, `test_db_matrix_unwritable_app_home_and_temp_falls_back_to_in_memory_red_banner`, `test_db_matrix_sentinel_selftest_and_failure_detection`, `test_file_backed_startup_uses_alembic_without_create_all`; `scripts/gates/guard_env_readers.py` |
| **P1.6 / `AC-27` / `DB-4` / `DB-5` / `DB-9`** | `energybaseline/db/backup.py::backup_sqlite_online`, `verify_backup_manifest`, `restore_sqlite_online` (`sqlite3.Connection.backup` + SHA-256 manifest + `PRAGMA integrity_check`) | `tests/gates/test_phase1_db_ladder.py::test_pre_migration_online_backup_and_validated_restore_on_failure` |
| **P1.7 / `SHL-6` / `SHL-9` / `LIF-5`** | `energybaseline/gui/page_status.py::StatusPage`; `energybaseline/gui/main_window.py::FallbackBanner`; `energybaseline/gui/app.py` (`excepthook`) | `tests/gui/test_phase1_gui.py::test_main_window_commands_path_guard_status_page_and_banners` |
| **P1.8 / `PKG-1` / `PKG-2` / `PKG-6`** | `scripts/build_exe.py` (`--onedir`, `--profile full\|lite`, `.lnk` shortcut, CycloneDX `sbom.cdx.json`, per-user Inno Setup / NSIS `--installer`) | `tests/gates/test_phase1_paths_and_commands.py::test_cyclonedx_sbom_generation`; Windows CI `scripts/evidence/installer_smoke.ps1` |
| **P1.10 (`E3`, `D2`)** | `scripts/evidence/{lif_orphans,startup_time,installer_smoke,dpi_matrix}.ps1` (`startup_time.ps1` running 5 cold + 5 warm starts with `median`/`p95`) | Windows CI job `131746122973` (`artifacts/phase1/evidence/*.json`) |

---

## 3. Commands Executed and Raw Output Paths (Canonical Gate `G1`)

Every command in the canonical Gate `G1` block (`PROMPT_PHASE1_EXECUTOR` §7) plus `guard_no_qthread_compute.py`, Alembic reversibility, and the 4 Windows `E3` scripts exited `0`:

| # | Gate `G1` Command | Env | Exit Code | Raw Output Path |
|---|---|---|---|---|
| 1 | `python -m pytest -q` | Local + 2-OS CI | `0` | `artifacts/phase1/gates_g1.txt` (`71 passed`) |
| 2 | `python -m pytest -q tests/gates` | Local + 2-OS CI | `0` | `artifacts/phase1/gates_g1.txt` (`48 passed`) |
| 3 | `python -m ruff check . && python -m ruff format --check .` | Local + 2-OS CI | `0` | `artifacts/phase1/gates_g1.txt` |
| 4 | `python -m pyflakes energybaseline` | Local + 2-OS CI | `0` | `artifacts/phase1/gates_g1.txt` |
| 5 | `python scripts/gates/guard_web_free.py` | Local + 2-OS CI | `0` | `artifacts/phase1/gates_g1.txt` |
| 6 | `python scripts/gates/guard_env_readers.py` | Local + 2-OS CI | `0` | `artifacts/phase1/gates_g1.txt` |
| 7 | `python scripts/gates/guard_tests_integrity.py` | Local + 2-OS CI | `0` | `artifacts/phase1/gates_g1.txt` |
| 8 | `python scripts/gates/guard_no_qthread_compute.py` | Local + 2-OS CI | `0` | `artifacts/phase1/gates_g1.txt` |
| 9 | `python scripts/gen_matrix.py --check` | Local + 2-OS CI | `0` | `artifacts/phase1/gates_g1.txt` (`249` IDs, `41 done`) |
| 10 | `python -m pytest -q --cov=energybaseline/core --cov-fail-under=60` | Local + 2-OS CI | `0` | `artifacts/phase1/gates_g1.txt` (`78.80%`) |
| 11 | `python -m pytest -q tests/gui -p no:cacheprovider` | Local + 2-OS CI | `0` | `artifacts/phase1/gates_g1.txt` (`3 passed`) |
| 12 | `test -z "$(git ls-files \| grep -Ei 'package.json\|tsconfig\|\\.tsx?$\|next\\.config\|drizzle\|eslint\|postcss')"` | Local + 2-OS CI | `0` | `artifacts/phase1/gates_g1.txt` |
| 13 | `test -z "$(git ls-files \| grep -E '__pycache__\|\\.pyc$')"` | Local + 2-OS CI | `0` | `artifacts/phase1/gates_g1.txt` |
| 14 | `test -z "$(grep -rn 'weasyprint' energybaseline scripts requirements.txt requirements.in requirements.lock)"` | Local + 2-OS CI | `0` | `artifacts/phase1/gates_g1.txt` |
| 15 | `test -z "$(grep -rn 'os.environ\["DATABASE_URL"\]' launcher.py)"` & `energybaseline/launcher.py` | Local + 2-OS CI | `0` | `artifacts/phase1/gates_g1.txt` |
| 16 | `pwsh -NoProfile -File scripts/evidence/lif_orphans.ps1` | `windows-latest` CI | `0` | `artifacts/phase1/evidence/lif_orphans.json` |
| 17 | `pwsh -NoProfile -File scripts/evidence/startup_time.ps1` | `windows-latest` CI | `0` | `artifacts/phase1/evidence/startup_time.json` |
| 18 | `pwsh -NoProfile -File scripts/evidence/installer_smoke.ps1 -Profile lite` | `windows-latest` CI | `0` | `artifacts/phase1/evidence/installer_smoke.json` |
| 19 | `pwsh -NoProfile -File scripts/evidence/dpi_matrix.ps1` | `windows-latest` CI | `0` | `artifacts/phase1/evidence/dpi_matrix.json` |

**GitHub Actions 2-OS CI Run (`046eab2230c6429066a0c5bef01fcd4290256efd`):**
- Workflow Run: `https://github.com/hajianamir/EnergyBaseline/actions/runs/43383391459` (`conclusion: success`)
- Windows Job (`windows-latest`): `https://github.com/hajianamir/EnergyBaseline/actions/runs/43383391459/job/131746122973` (`conclusion: success`)
- Linux Job (`ubuntu-latest`): `https://github.com/hajianamir/EnergyBaseline/actions/runs/43383391459/job/131746122913` (`conclusion: success`)

---

## 4. `pytest -q` Summary and Measured Coverage

- **`pytest -q` Summary:** **`71 passed, 0 failed, 0 skipped, 0 xfailed`** (`48 passed` in `tests/gates`, `3 passed` in `tests/gui`).
- **Measured `energybaseline/core` Line Coverage (`S5 >= 60%`):** **`78.80%`** (`3991` statements, `846` missed).
- **Line Coverage of Phase 1 Touched Modules (`artifacts/phase1/pytest_full.txt`):**

| Module | Stmts | Miss | Cover |
|---|---:|---:|---:|
| `energybaseline/config/settings.py` | 119 | 15 | **87%** |
| `energybaseline/db/backup.py` | 87 | 10 | **89%** |
| `energybaseline/db/migrate.py` | 15 | 0 | **100%** |
| `energybaseline/db/session.py` | 277 | 35 | **87%** |
| `energybaseline/core/pool.py` | 651 | 223 | **66%** |
| `energybaseline/gui/commands.py` | 34 | 2 | **94%** |
| `energybaseline/gui/main_window.py` | 201 | 32 | **84%** |
| `energybaseline/gui/page_status.py` | 173 | 25 | **86%** |
| `energybaseline/gui/single_instance.py` | 81 | 23 | **72%** |
| `energybaseline/gui/pages_setup.py` | 440 | 228 | **48%** |
| `energybaseline/io/paths.py` | 180 | 54 | **70%** |

---

## 5. Evidence Artifacts and SHA-256 Manifest

### 5.1 Repository Evidence & Frozen Invariant Hashes

| Artifact Path | SHA-256 |
|---|---|
| `LICENSE` | `1c633e61e328b34b883571ae25a92cf6a390e6f2752871331545d70a5b553afe` |
| `tests/.baseline.sha256` | `6f3abde78940ac71c104a32305c7a69bc2a9c90a09f6660a332de0eac467f3dc` |
| `docs/requirements.yaml` | `abea07502b3b7fd56c69665e5e13967b7feb68daf20102cb54f7e9e35d297624` |
| `docs/requirements_matrix.md` | `7978ebf15b444e0e2f417b29f8d43fcc2d7d3c361fe7a41ac2f8aef23e64bc34` |
| `artifacts/phase1/gates_g1.txt` | `d8daef4acede5c1903b4039a9e734aca4a38282ca10e98a2297039c5384581c2` |
| `artifacts/phase1/pytest_gates_and_db.txt` | `ddc08874e855f5a9ad3aaf6e0ea5292057a7589b2a452aba9c48c8b958cec1f2` |
| `artifacts/phase1/pytest_gui.txt` | `f7062b39e9e3c65b24df5545d52419fffa304b027e341998230e3c7ab04325cc` |
| `artifacts/phase1/pytest_full.txt` | `b7bf28c1fc1a9f693c02d32d508475b036266b62427f3913b99e42fb4f862868` |
| `artifacts/phase1/sbom_verification.txt` | `51099d57a23f8d926a2a9708f1fe0eb3d26cd958efe03043f64b657837eaa954` |
| `artifacts/phase1/ci_links.txt` | `3885fd2cefaaa22de3f4faf790e3b79a1493f1a7dee9de9a22a7adfcd7283a9d` |
| `artifacts/phase1/code_commit.txt` | `cf77233539e3ed2419b9e6a0c619fa110f5ea1452507a003a5186a5e44124c58` |
| `artifacts/phase1/git_diff_stat.txt` | `130c1ed160d62dddbf05a3210cb6804c716b25c265257f560bf4b6d1a783021f` |
| `artifacts/phase1/evidence/installer_smoke.json` | `acb11308c60b6e069e2af1207c5d2c0003cbd8af3cd723543ec0a776ec596485` (CI raw `sha256: 292a1617cedbc3c042c5a144648e80810493ea5ed8a2c6075411c60922a5c522`) |
| `artifacts/phase1/evidence/lif_orphans.json` | `fb9a103e57c402f2b19b69403eb90fcb9d86550016dbc4a5c26e5dd7f1dab54a` (CI raw `sha256: 4140a7e4d1ec30f60168e1d8a9d9dc961d9c86a6caa3cf1f563d782e04e9a4f3`) |
| `artifacts/phase1/evidence/startup_time.json` | `39f25613f9c721cf9de5a1661ca38d568871fc9aa954b8a855d463a18ee29d5d` (CI raw `sha256: b4d529e20e1dbbde44a3235615e8ef1957b3ec046efdb62ee84fe647cdb7b293`) |
| `artifacts/phase1/evidence/dpi_matrix.json` | `5e32d67633f7939f15c5c166618bb2856c4812b440a417115f913292fc8f2cc1` (CI raw `sha256: 9e5a3f22227a499142295f273377f72e5b6d309156e549b18c165d2d17f98043`) |

### 5.2 Built Windows Packaging Deliverables (`windows-latest` job `131746122973`, `--profile lite --installer`)

| Windows Build Deliverable | SHA-256 (`installer_smoke.json`) |
|---|---|
| `dist\EnergyBaseline\EnergyBaseline.exe` (`onedir`) | `d5305142b8d46c2b543090bac58e02d505671b8bf3437be71c10e00ffbc18127` |
| `dist\EnergyBaseline\EnergyBaseline.lnk` | `62a253d7aa3474ef524fe02351dd7d70ff18f61bac123e210438036edf881a03` |
| `dist\EnergyBaseline\sbom.cdx.json` (143 components) | `fd23aff534c7bc90f03c1157e4843e67c44ec5b02e0d647fc16f173aefb5aa2d` |
| `dist\EnergyBaseline-Setup-lite.exe` (Inno Setup per-user) | `eb3edb5f129e3aedf8c8075bb57c89efbc692d882de0c2c06cd890b3af25f084` |

---

## 6. `E3` (Windows) Evidence — Logs, Timings, and Hardware Classification (`D2 = B+A`)

> **Hardware Classification (`DECISIONS.md` `D2 = B+A`):**
> All four Windows runtime scripts below executed on GitHub Actions `windows-latest` (`Microsoft Windows Server 2025 Datacenter`, build `26100`, `AMD EPYC 9V74`, `4` logical vCPUs, `15.99 GB` RAM, `1024×768` virtual display) against the packaged PyInstaller `onedir` executable `dist\EnergyBaseline\EnergyBaseline.exe`. Per ratified Decision **`D2 = B+A`**, these CI results are explicitly labelled **`CI, non-normative`**; normative performance/visual sign-off targets physical reference hardware (`8-core · 16 GB · NVMe · Win11 23H2 · 1920×1080@100%`).

1. **Orphan & Windows Job Object Verification (`artifacts/phase1/evidence/lif_orphans.json`, `pass: true`):**
   - **Clean quit during active worker run:** `parent_exit_code = 0`, `spawned_worker_pids = [6652]`, `wal_checkpointed = true`, **`cleanup_elapsed_seconds = 0.0553 s`** (`<= 5.0 s`), **`surviving_orphan_pids = []`**.
   - **Abrupt parent termination (`Stop-Process -Force`) during uncooperative worker run:** `job_object_active = true` (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`), `job_object_assigned_pids = [4184]`, **`cleanup_elapsed_seconds = 0.0407 s`** (`<= 5.0 s`), **`surviving_orphan_pids = []`**, **`orphan_count = 0`**.
2. **Per-User Installer Smoke (`artifacts/phase1/evidence/installer_smoke.json`, `pass: true`):**
   - Compiled `EnergyBaseline-Setup-lite.exe` via Inno Setup (`PrivilegesRequired=lowest`, zero elevation).
   - Silent per-user install (`install_exit_code = 0`) → launched installed `EnergyBaseline.exe` (`launch_exit_code = 0`, `launch_db_ok = true`, `first_paint_ms = 2286.26 ms`) → silent per-user uninstall (`uninstall_exit_code = 0`, `uninstalled_cleanly = true`).
3. **Startup Time Benchmark — 5 Cold + 5 Warm Starts (`artifacts/phase1/evidence/startup_time.json`, `pass: true`, `CI, non-normative`):**
   - **5 Cold Starts (`cold_1..5`, each against a fresh uninitialized `EB_HOME`):** `2271.40 ms`, `2225.50 ms`, `2213.75 ms`, `2189.62 ms`, `2200.23 ms` → **`cold_median_ms = 2213.75 ms`**, **`cold_p95_ms = 2262.22 ms`**.
   - **5 Warm Starts (`warm_1..5`, against initialized `EB_HOME`):** `2156.17 ms`, `2147.10 ms`, `2125.88 ms`, `2121.65 ms`, `2124.45 ms` → **`warm_median_ms = 2125.88 ms`**, **`warm_p95_ms = 2154.36 ms`** (`CI, non-normative` on shared 4-vCPU VM).
4. **High-DPI Scaling Sweep (`artifacts/phase1/evidence/dpi_matrix.json`, `pass: true`, `CI, non-normative`):**
   - `100%` (`dpr = 1.0`): client `1000×672`, frame `1000×703` inside `1024×720` (`fits_width = true`, `fits_height = true`, `exit = 0`).
   - `125%` (`dpr = 1.25`): client `795×528`, frame `795×553` inside `819×576` (`fits_width = true`, `fits_height = true`, `exit = 0`).
   - `150%` (`dpr = 1.5`): client `659×432`, frame `659×453` inside `683×480` (`fits_width = true`, `fits_height = true`, `exit = 0`).
   - `175%` (`dpr = 1.75`): client `561×363`, frame `561×381` inside `585×411` (`fits_width = true`, `fits_height = true`, `exit = 0`).

---

## 7. Deltas in `docs/deviations.md` / `docs/assumptions.md` / `docs/not_implemented.md`

- **`docs/deviations.md`:** Zero new deviations introduced in Phase 1 (`DEV-01` through `DEV-04` from Phase 0 remain unchanged).
- **`docs/assumptions.md`:** Zero changes (`S1`–`S7` ratified in Phase 0 remain unchanged and enforced).
- **`docs/not_implemented.md`:** Zero changes (`LIF-4` `[O]` optional system tray integration remains documented as deferred; updated from `planned` to `deferred` in `docs/requirements.yaml`).

---

## 8. What Was Not Proven and Why

1. **Normative `D2` Physical Reference Hardware Benchmarks & Visual Screenshots (`8-core · 16 GB · NVMe · Win11 23H2 · 1920×1080@100%`):**
   - Per ratified Decision `D2 = B+A`, the Executor operates inside a Linux container paired with GitHub Actions `windows-latest` (`4-vCPU` virtualized Windows Server 2025 with a `1024×768` virtual display adapter) and has no physical Windows 11 23H2 workstation attached.
   - Consequently, while `startup_time.ps1` (`5 cold` `median = 2213.75 ms` / `p95 = 2262.22 ms`; `5 warm` `median = 2125.88 ms` / `p95 = 2154.36 ms`) and `dpi_matrix.ps1` (`100%/125%/150%/175%` programmatic geometry bounds) succeeded against the packaged `EnergyBaseline.exe` on `windows-latest`, they are reported strictly as **`CI, non-normative`**. Normative warm-start budget verification (`<= 1500 ms` on physical 8-core NVMe Win11 23H2) and human visual inspection of font rendering/screenshots on a physical `1920×1080` monitor at OS-level Windows Display Settings `100%/125%/150%/175%` remain to be executed on the physical `D2` reference machine.
2. **`--profile full` Windows Installer Build Time in Per-Push CI:**
   - `scripts/build_exe.py` supports both `--profile full` and `--profile lite` unchanged (`D3 = A`). Per-push CI runs `--profile lite --installer` (~3 min build) to keep CI feedback under 14 minutes; `--profile full` (bundling CPU `torch==2.15.0`) uses the identical `--onedir` + Inno Setup pipeline and is exercised in release packaging (Phase 9).
3. **Downstream Phase 2–8 Domain Modules:**
   - Per strict Phase 1 scope rules, Data/Time/Column Setup (Phase 2), VSG + TCP + Cache (Phase 3), T1–T14 test catalog (Phase 4), model store SHA-256 governance & `pickle` eradication in `store.py` (Phase 6), SQL connector DPAPI/secret-lifetime hardening (Phase 7), and ReportLab PDF renderer (Phase 8) were intentionally left untouched.

---

## 9. Open Risks and Their Effect on Phase 2

1. **Shared Virtual Runner Timing Variance (`Low` impact on Phase 2):**
   - Warm startup on 4-vCPU `windows-latest` (`median = 2.13 s`) reflects Windows Defender real-time scanning and cold hypervisor I/O on ephemeral CI VMs rather than physical NVMe desktop storage. Keeping heavy scientific/ML imports (`torch`, `optuna`, `statsmodels`) lazy and inside worker processes ensures Phase 2 (`Data / Time / Column Setup`) UI additions will not regress startup latency.
2. **Windows SQLite File-Handle Discipline (`Mitigated`, monitor in Phase 2):**
   - `SQLAlchemy==2.1.8` holds pooled SQLite file handles unless `reset_engine()` / `checkpoint_and_close()` or `NullPool` is used before file replacement. Phase 1 centralized this in `energybaseline/db/session.py` and `energybaseline/db/backup.py`; Phase 2 schema additions (column roles, quantity types, NRE, and outlier policy tables/migrations) will automatically inherit pre-migration online backup and validated restore.
3. **Worker Envelope Serialization Discipline for Phase 2 (`Low` impact, ready):**
   - `AnalysisPoolManager` enforces `> 1 MiB` cache staging (`{path, sha256, rows}`) and `_EB_IN_WORKER=1` SQLite isolation; Phase 2 ingest/temporal/aggregation transformations running inside the pipeline must continue returning plain Python/NumPy/Pandas data structures without ORM session objects or Qt widgets.
