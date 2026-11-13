# Phase 1 Executor Report — Lifecycle & Skeleton (`G1`)

- **Date:** `2026-11-12` (UTC; Windows CI run completed `2026-11-13T00:33Z`)
- **Executor Branch:** `arena/3611a52f-energybaseline` (sole push target; `main` and `arena/af928d3c-energybaseline` untouched)
- **Phase 0 Base Commit:** `23fab18bd21e5f827d41e0b7c1ff80a59edb2077`
- **Language Compliance (`G-7`):** 100% English across all code, docstrings, comments, PowerShell scripts, tests, workflows, commit messages, and reports.
- **License & Frozen Baseline Integrity (`S7`, `D0.3`):** `LICENSE` (`sha256: 1c633e61e328b34b883571ae25a92cf6a390e6f2752871331545d70a5b553afe`), `tests/.baseline.sha256` (`sha256: 6f3abde78940ac71c104a32305c7a69bc2a9c90a09f6660a332de0eac467f3dc`), `artifacts/review_baseline/`, and `artifacts/phase0/` untouched.

---

## 1. Phase ID, Full Commit SHA, and `git diff --stat`

- **Phase ID:** `Phase 1 — Lifecycle & Skeleton (Gate G1)`
- **Code Commit Verified Green on 2-OS CI:** `f37af6aa4be1272566304e8e1eb51d6282a91ca3` (short: `f37af6a`, preceding this final `artifacts/phase1/` evidence commit on linear history `23fab18..HEAD`)
- **Permitted File Scope (`EXECUTION_PLAN.md` §5.3):** Strictly honored — zero edits outside `energybaseline/launcher.py`, `energybaseline/gui/*`, `energybaseline/core/pool.py` (transport adapter only; pipeline/models/nn/mining/store untouched), `energybaseline/db/*`, `energybaseline/config/*`, `energybaseline/io/*`, `scripts/build_exe.py`, `alembic/env.py`, `scripts/gates/guard_no_qthread_compute.py`, `scripts/evidence/*`, `tests/gates/*`, `tests/gui/*`, `.github/workflows/ci.yml`, `CHANGELOG.md`, `docs/requirements.yaml`, and `docs/requirements_matrix.md`.

```text
 .github/workflows/ci.yml                      |   50 +-
 CHANGELOG.md                                  |   18 +-
 alembic/env.py                                |    3 +
 docs/requirements.yaml                        |  994 ++++++++++++++--------
 docs/requirements_matrix.md                   |   64 +-
 energybaseline/config/__init__.py             |    6 +
 energybaseline/config/settings.py             |  117 ++-
 energybaseline/core/pool.py                   | 1115 +++++++++++++++++++++++++
 energybaseline/db/__init__.py                 |   31 +-
 energybaseline/db/backup.py                   |  156 ++++
 energybaseline/db/migrate.py                  |    9 +-
 energybaseline/db/session.py                  |  489 +++++++++--
 energybaseline/gui/app.py                     |  196 ++++-
 energybaseline/gui/commands.py                |   81 ++
 energybaseline/gui/main_window.py             |  213 ++++-
 energybaseline/gui/page_status.py             |  297 ++++++-
 energybaseline/gui/pages_setup.py             |  378 ++++++---
 energybaseline/gui/single_instance.py         |  113 +++
 energybaseline/io/__init__.py                 |   21 +
 energybaseline/io/paths.py                    |  272 ++++++
 energybaseline/launcher.py                    |   54 +-
 scripts/build_exe.py                          |  384 +++++++--
 scripts/evidence/dpi_matrix.ps1               |   99 +++
 scripts/evidence/installer_smoke.ps1          |  146 ++++
 scripts/evidence/lif_orphans.ps1              |  164 ++++
 scripts/evidence/startup_time.ps1             |  121 +++
 scripts/gates/guard_no_qthread_compute.py     |  182 ++++
 tests/gates/test_phase1_db_ladder.py          |  235 ++++++
 tests/gates/test_phase1_paths_and_commands.py |  197 +++++
 tests/gates/test_phase1_pool_and_lifecycle.py |  172 ++++
 tests/gui/test_phase1_gui.py                  |  101 +++
 31 files changed, 5756 insertions(+), 722 deletions(-)
```

---

## 2. ID → File / Function → Test Table

| Requirement / Work ID | File & Function / Class | Verification Test(s) / Gate(s) |
|---|---|---|
| **P1.1 / `R-1` / `G-3`** | `energybaseline/core/pool.py::AnalysisPoolManager`, `run_in_pool`, `compute_max_workers`; `energybaseline/gui/pages_setup.py::AnalysisPage`; `scripts/gates/guard_no_qthread_compute.py` | `tests/gates/test_phase1_pool_and_lifecycle.py::test_compute_max_workers_formula`, `test_run_in_pool_executes_analysis_end_to_end_and_clean_shutdown`; `tests/gates/test_phase1_paths_and_commands.py::test_guard_no_qthread_compute_passes_repo_and_catches_synthetic` |
| **P1.1 / `SHL-5`** | `energybaseline/core/pool.py::_worker_initializer` (`_EB_IN_WORKER=1`); `energybaseline/db/session.py::_assert_not_worker`; UI-thread-only `store.new_run` / `store.save_run` in `AnalysisPage` | `tests/gates/test_phase1_pool_and_lifecycle.py::test_pool_handshake_shl5_worker_db_guard_and_hard_kill` |
| **P1.1 / `IPC-1`** | `energybaseline/core/pool.py::AnalysisPoolManager._ensure_pool` (`mp.get_context("spawn")`); `energybaseline/launcher.py::main` (`multiprocessing.freeze_support()`) | `tests/gates/test_phase1_pool_and_lifecycle.py::test_run_in_pool_executes_analysis_end_to_end_and_clean_shutdown`; `scripts/evidence/lif_orphans.ps1` |
| **P1.1 / `IPC-2` / `IPC-6` / `IPC-7`** | `energybaseline/core/pool.py::pack_payload`, `unpack_payload`, `handshake` (`PROTOCOL_VERSION = "1.0"`, `> 1 MiB` `{path, sha256, rows}` under `APP_HOME/cache/`, deterministic temp cleanup) | `tests/gates/test_phase1_pool_and_lifecycle.py::test_payload_over_1mib_uses_cache_file_ref_and_checks_sha256`, `test_pool_handshake_shl5_worker_db_guard_and_hard_kill` |
| **P1.1 / `IPC-3` / `IPC-5`** | `energybaseline/core/pool.py::_drain_queue_loop`, `_dispatch_worker_command` (`mp.Queue` pipe IPC, crash/error isolation) | `tests/gates/test_phase1_pool_and_lifecycle.py::test_run_in_pool_executes_analysis_end_to_end_and_clean_shutdown` |
| **P1.1 / `IPC-4` / `SHL-4`** | `energybaseline/core/pool.py::CancelToken` (stage, fold, test, epoch, trial callbacks), `_worker_inner_cancel_hooks`, `PoolJobHandle._on_grace_expired` (`10 s` hard-kill) | `tests/gates/test_phase1_pool_and_lifecycle.py::test_cancel_token_callbacks_raise_interrupted_error`, `test_pool_handshake_shl5_worker_db_guard_and_hard_kill` |
| **P1.2 / `LIF-1` / `LIF-2`** | `energybaseline/gui/app.py::perform_clean_shutdown` (`aboutToQuit` -> cancel -> join `5 s` -> hard stop -> WAL checkpoint -> DB close); `energybaseline/core/pool.py::WindowsJobObject` (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`) | `tests/gates/test_phase1_pool_and_lifecycle.py::test_pool_handshake_shl5_worker_db_guard_and_hard_kill`; Windows CI `scripts/evidence/lif_orphans.ps1` |
| **P1.3 / `AC-02` / `SHL-1`** | `energybaseline/gui/single_instance.py::SingleInstanceGuard` (`QLocalServer` / `QLocalSocket` primary activation + secondary exit `0`) | `tests/gui/test_phase1_gui.py::test_single_instance_guard_activates_primary_and_rejects_second` |
| **P1.4 / `SHL-3` / `SHL-4` (`AC-04`)** | `energybaseline/io/paths.py::validate_path`, `resolve_app_home`, `resolve_picked_path`, `resolve_temp_dir`, `resolve_cache_dir`; `energybaseline/gui/pages_setup.py::SetupPage.open_file` | `tests/gates/test_phase1_paths_and_commands.py::test_validate_path_accepts_inside_allowed_root`, `test_validate_path_rejects_traversal_and_unc`, `test_validate_path_rejects_outside_allowed_roots`, `test_validate_path_rejects_leaf_and_intermediate_symlinks`, `test_resolve_app_home_precedence`; `tests/gui/test_phase1_gui.py::test_main_window_commands_path_guard_status_page_and_banners` |
| **P1.5 / `SCR-7`** | `energybaseline/gui/commands.py::STATIC_UI_COMMAND_SPECS`, `CommandRegistry`; `energybaseline/core/pool.py::WORKER_COMMAND_REGISTRY`; startup validation in `MainWindow.__init__` | `tests/gates/test_phase1_paths_and_commands.py::test_command_registries_are_compile_time_immutable_and_reject_unknown`, `test_no_eval_exec_or_getattr_in_command_dispatchers` |
| **P1.6 / `DB-1` / `DB-2` / `DB-11` / `AC-05`** | `energybaseline/config/settings.py::resolve_database_url`; `energybaseline/db/session.py::get_engine`, `query_sqlite_pragmas`, `init_database` (`%LOCALAPPDATA%` / `portable.flag` -> `%TEMP%` `"data may be lost"` -> `:memory:` red banner; 3 s external probe + audit; full SQLite PRAGMAs); `energybaseline/launcher.py` (`DATABASE_URL` override removed) | `tests/gates/test_phase1_db_ladder.py::test_launcher_does_not_override_database_url`, `test_db_matrix_no_env_and_whitespace_env_and_pragmas`, `test_db_matrix_unreachable_postgres_probe_audits_and_warns`, `test_db_matrix_unwritable_app_home_falls_back_to_temp_rung`, `test_db_matrix_unwritable_app_home_and_temp_falls_back_to_in_memory_red_banner`; `scripts/gates/guard_env_readers.py` |
| **P1.6 / `AC-27` / `DB-4` / `DB-5` / `DB-9`** | `energybaseline/db/backup.py::backup_sqlite_online`, `verify_backup_manifest`, `restore_sqlite_online` (`sqlite3.Connection.backup` + SHA-256 manifest + `PRAGMA integrity_check`) | `tests/gates/test_phase1_db_ladder.py::test_pre_migration_online_backup_and_validated_restore_on_failure` |
| **P1.7 / `SHL-6` / `SHL-9` / `LIF-5`** | `energybaseline/gui/page_status.py::StatusPage`; `energybaseline/gui/main_window.py::FallbackBanner`; `energybaseline/gui/app.py` (`excepthook`) | `tests/gui/test_phase1_gui.py::test_main_window_commands_path_guard_status_page_and_banners` |
| **P1.8 / `PKG-1` / `PKG-2` / `PKG-6`** | `scripts/build_exe.py` (`--onedir`, `--profile full\|lite`, `.lnk` shortcut, CycloneDX `sbom.cdx.json`, per-user Inno Setup / NSIS `--installer`) | `tests/gates/test_phase1_paths_and_commands.py::test_cyclonedx_sbom_generation`; Windows CI `scripts/evidence/installer_smoke.ps1` |
| **P1.10 (`E3`, `D2`)** | `scripts/evidence/{lif_orphans,startup_time,installer_smoke,dpi_matrix}.ps1` | Windows CI job `131594140697` (`artifacts/phase1/evidence/*.json`) |

---

## 3. Commands Executed and Raw Output Paths (Gate `G1`)

All 14 Gate `G1` commands (`EXECUTION_PLAN.md` §4) exited `0`:

| # | Gate `G1` Command | Env | Exit Code | Raw Output Path |
|---|---|---|---|---|
| 1 | `python scripts/gates/guard_web_free.py` | Local + 2-OS CI | `0` | `artifacts/phase1/gates_g1.txt` |
| 2 | `python scripts/gates/guard_env_readers.py` | Local + 2-OS CI | `0` | `artifacts/phase1/gates_g1.txt` |
| 3 | `python scripts/gates/guard_tests_integrity.py` | Local + 2-OS CI | `0` | `artifacts/phase1/gates_g1.txt` |
| 4 | `python scripts/gates/guard_no_qthread_compute.py` | Local + 2-OS CI | `0` | `artifacts/phase1/gates_g1.txt` |
| 5 | `test -z "$(grep -rn 'os.environ\["DATABASE_URL"\]' energybaseline/launcher.py)"` | Local + 2-OS CI | `0` | `artifacts/phase1/gates_g1.txt` |
| 6 | `ruff check . && ruff format --check . && pyflakes energybaseline` | Local + 2-OS CI | `0` | `artifacts/phase1/gates_g1.txt` |
| 7 | `python -m alembic upgrade head && python -m alembic downgrade base && python -m alembic upgrade head` | Local + 2-OS CI | `0` | `artifacts/phase1/gates_g1.txt` |
| 8 | `pytest -q tests/gates/ tests/test_config_db.py` | Local + 2-OS CI | `0` | `artifacts/phase1/pytest_gates_and_db.txt` |
| 9 | `pytest -q tests/gui/` | Local + 2-OS CI | `0` | `artifacts/phase1/pytest_gui.txt` |
| 10 | `pytest -q` (full test suite + coverage) | Local + 2-OS CI | `0` | `artifacts/phase1/pytest_full.txt` |
| 11 | `pwsh -NoProfile -File scripts/evidence/lif_orphans.ps1` | `windows-latest` CI | `0` | `artifacts/phase1/evidence/lif_orphans.json` |
| 12 | `pwsh -NoProfile -File scripts/evidence/startup_time.ps1` | `windows-latest` CI | `0` | `artifacts/phase1/evidence/startup_time.json` |
| 13 | `pwsh -NoProfile -File scripts/evidence/installer_smoke.ps1 -Profile lite` | `windows-latest` CI | `0` | `artifacts/phase1/evidence/installer_smoke.json` |
| 14 | `pwsh -NoProfile -File scripts/evidence/dpi_matrix.ps1` | `windows-latest` CI | `0` | `artifacts/phase1/evidence/dpi_matrix.json` |

**GitHub Actions 2-OS CI Run (`f37af6a`):**
- Workflow Run: `https://github.com/hajianamir/EnergyBaseline/actions/runs/43336572378` (`conclusion: success`)
- Windows Job (`windows-latest`): `https://github.com/hajianamir/EnergyBaseline/actions/runs/43336572378/job/131594140697` (`conclusion: success`)
- Linux Job (`ubuntu-latest`): `https://github.com/hajianamir/EnergyBaseline/actions/runs/43336572378/job/131594141112` (`conclusion: success`)

---

## 4. `pytest -q` Summary and Coverage of Touched Modules

- **`pytest -q` Summary:** **`69 passed, 0 failed, 0 skipped, 0 xfailed`** (`147.94 s` with full-package coverage instrumentation; `88.51 s` uninstrumented).
- **`energybaseline/core` Coverage Gate (`S5 >= 60%`):** **`78.85%`** (`3991` statements, `844` missed).
- **Line Coverage of Phase 1 Touched Modules (`artifacts/phase1/pytest_full.txt`):**

| Module | Stmts | Miss | Cover | Notes |
|---|---:|---:|---:|---|
| `energybaseline/config/settings.py` | 119 | 15 | **87%** | Sole DB env reader; APP_HOME & temp dir helpers |
| `energybaseline/db/backup.py` | 87 | 10 | **89%** | SQLite Online Backup API + SHA-256 manifest + restore |
| `energybaseline/db/migrate.py` | 15 | 0 | **100%** | Programmatic Alembic runner (`eb_target_url`) |
| `energybaseline/db/session.py` | 277 | 38 | **86%** | 3 s probe, fallback ladder, PRAGMAs, worker guard, WAL checkpoint |
| `energybaseline/core/pool.py` | 651 | 220 | **66%** | Spawn pool, `mp.Queue`, `>1 MiB` refs, `CancelToken`, `WindowsJobObject` (Win32 branches exercised on `windows-latest`) |
| `energybaseline/gui/commands.py` | 34 | 2 | **94%** | Static compile-time `MappingProxyType` command registry |
| `energybaseline/gui/main_window.py` | 201 | 32 | **84%** | `FallbackBanner`, command wiring, High-DPI geometry clamping |
| `energybaseline/gui/page_status.py` | 173 | 25 | **86%** | Live DB/pool/cache/artifact/orphan operational dashboard |
| `energybaseline/gui/single_instance.py` | 81 | 23 | **72%** | `QLocalServer` / `QLocalSocket` single-instance guard |
| `energybaseline/gui/pages_setup.py` | 440 | 228 | **48%** | `SetupPage.open_file` path guard + `AnalysisPage` pool polling |
| `energybaseline/io/paths.py` | 180 | 54 | **70%** | Normalized path validation, UNC/traversal/symlink rejection |

*(Note: `energybaseline/launcher.py` and `energybaseline/gui/app.py` CLI boot paths are exercised out-of-process by `installer_smoke.ps1`, `lif_orphans.ps1`, `startup_time.ps1`, and `dpi_matrix.ps1` against the frozen `EnergyBaseline.exe`.)*

---

## 5. Artifacts and SHA-256 Manifest

### 5.1 Repository Evidence & Frozen Invariant Hashes

| Artifact Path | SHA-256 |
|---|---|
| `LICENSE` | `1c633e61e328b34b883571ae25a92cf6a390e6f2752871331545d70a5b553afe` |
| `tests/.baseline.sha256` | `6f3abde78940ac71c104a32305c7a69bc2a9c90a09f6660a332de0eac467f3dc` |
| `docs/requirements.yaml` | `536151b223004833cd1c84a9cccca3ee135231576c753c4fa146b9fbb79a677c` |
| `docs/requirements_matrix.md` | `7327d96c2275a0e8d916d6dd80ce355b0f42344230a76929393a8411e82f9713` |
| `artifacts/phase1/gates_g1.txt` | `d82460e78158c21ce4a1a7f1d5492b0a3dc2b5d884357040d188c3524f5dba51` |
| `artifacts/phase1/pytest_gates_and_db.txt` | `dca20fa65f059fb4c1eafbb43b774ce127acf906d77f04b3d770d8ffd9021700` |
| `artifacts/phase1/pytest_gui.txt` | `8b36c0742d881096e51dba7e156c72b58b885678909cac0257926bd031ce25b4` |
| `artifacts/phase1/pytest_full.txt` | `40c19532853dea7bb615ef8c787b442a365bf95bf4c09b3baab627486a5cf990` |
| `artifacts/phase1/sbom_verification.txt` | `51099d57a23f8d926a2a9708f1fe0eb3d26cd958efe03043f64b657837eaa954` |
| `artifacts/phase1/ci_links.txt` | `dfa7134cb7322ae8b8f1f2d761d1e863b2b5aaf109a477ca5a86bf680d3b8c64` |
| `artifacts/phase1/git_diff_stat.txt` | `8725c7ce903d7fd87b25fb17473a7e801f4de7050024ca6b90a60401767c653d` |
| `artifacts/phase1/evidence/installer_smoke.json` | `04e4e281170fdaa49a55aa7d3d144c08365c4d80223ca035ba701826a57cdebb` (CI raw file `sha256: e31720cec75e8ba3e6dda2f7a6a2242146610681222ef313c1cffefd19cd3c25`) |
| `artifacts/phase1/evidence/lif_orphans.json` | `d2a333248cbf223333a6263dce2543b0fb64cea903789b41d5c5bc2637ad32f7` (CI raw file `sha256: 4aea21ea9f80de053b187275143554f2471553d3192f6edd0d964dbbb02f64b8`) |
| `artifacts/phase1/evidence/startup_time.json` | `6ed4ade938f19b2ab4d2a55031b3ed1b93224401c5c87ea8162a3da479f58270` (CI raw file `sha256: 6bd27bceb23203af090a8ce10b98993fcc8dd4a7e8edec703de28ad249276486`) |
| `artifacts/phase1/evidence/dpi_matrix.json` | `374cb1ea65d8c2cf8e8208d8614fd7152d082f5c242aa7d9b4edc49750b319c0` (CI raw file `sha256: 083bdaabe47353d7a02658644644ea91392c06f89fb9f7cc31e999bb539b5562`) |

### 5.2 Built Windows Packaging Deliverables (`windows-latest` job `131594140697`, `--profile lite --installer`)

| Windows Build Deliverable | SHA-256 (`installer_smoke.json`) |
|---|---|
| `dist\EnergyBaseline\EnergyBaseline.exe` (`onedir`) | `a31e84590f7a29c5304888a7d3570abbbf17a7b5078ade3e0bdf6342a92bf02d` |
| `dist\EnergyBaseline\EnergyBaseline.lnk` | `5a2745d332a7693dc8c963c98d6ad0ec5436fbdf825368a4064aaf70216ba58a` |
| `dist\EnergyBaseline\sbom.cdx.json` (143 components) | `fd23aff534c7bc90f03c1157e4843e67c44ec5b02e0d647fc16f173aefb5aa2d` |
| `dist\EnergyBaseline-Setup-lite.exe` (Inno Setup per-user) | `fa7871a573dc45516e7285554959b9dcb2fc5ab8de216a3e5f10a77fa316ebb7` |

---

## 6. `E3` (Windows) Evidence — Logs, Timings, and Hardware Classification (`D2 = B+A`)

> **Hardware Classification (`DECISIONS.md` `D2 = B+A`):**
> All four Windows runtime scripts below executed on GitHub Actions `windows-latest` (`Microsoft Windows Server 2025 Datacenter`, build `26100`, `AMD EPYC 9V45`, `4` logical vCPUs, `15.99 GB` RAM, `1024×768` virtual display) against the packaged PyInstaller `onedir` executable `dist\EnergyBaseline\EnergyBaseline.exe`. Per ratified Decision **`D2 = B+A`**, these CI results are explicitly labelled **`CI, non-normative`**; normative performance/visual sign-off targets physical reference hardware (`8-core · 16 GB · NVMe · Win11 23H2 · 1920×1080@100%`).

1. **Orphan & Windows Job Object Verification (`artifacts/phase1/evidence/lif_orphans.json`, `pass: true`):**
   - **Clean quit during active worker run:** `parent_exit_code = 0`, `spawned_worker_pids = [2068]`, `wal_checkpointed = true`, **`cleanup_elapsed_seconds = 0.046 s`** (`<= 5.0 s`), **`surviving_orphan_pids = []`**.
   - **Abrupt parent termination (`Stop-Process -Force`) during uncooperative worker run:** `job_object_active = true` (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`), `job_object_assigned_pids = [2432]`, **`cleanup_elapsed_seconds = 0.0391 s`** (`<= 5.0 s`), **`surviving_orphan_pids = []`**, **`orphan_count = 0`**.
2. **Per-User Installer Smoke (`artifacts/phase1/evidence/installer_smoke.json`, `pass: true`):**
   - Compiled `EnergyBaseline-Setup-lite.exe` via Inno Setup (`PrivilegesRequired=lowest`, zero elevation).
   - Silent per-user install (`install_exit_code = 0`) → launched installed `EnergyBaseline.exe` (`launch_exit_code = 0`, `launch_db_ok = true`, `first_paint_ms = 1781.7 ms`) → silent per-user uninstall (`uninstall_exit_code = 0`, `uninstalled_cleanly = true`).
3. **Startup Time Telemetry (`artifacts/phase1/evidence/startup_time.json`, `pass: true`, `CI, non-normative`):**
   - **Cold launch (`cold_1`):** `first_paint_ms = 1684.24 ms` (`total_process_ms = 2372.03 ms`, `db_ok = true`).
   - **Warm launches (`warm_1..3`):** `1641.14 ms`, `1637.28 ms`, `1615.60 ms` (`warm_first_paint_median_ms = 1637.28 ms` on shared 4-vCPU CI VM; `CI, non-normative`).
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
   - Consequently, while `startup_time.ps1` (`cold = 1684.24 ms`, `warm median = 1637.28 ms`) and `dpi_matrix.ps1` (`100%/125%/150%/175%` programmatic geometry bounds) succeeded against the packaged `EnergyBaseline.exe` on `windows-latest`, they are reported strictly as **`CI, non-normative`**. Normative warm-start budget verification (`<= 1500 ms` on 8-core NVMe Win11 23H2) and human visual inspection of font rendering/screenshots on a physical `1920×1080` monitor at OS-level Windows Display Settings `100%/125%/150%/175%` remain to be executed on the physical `D2` reference machine (scheduled for final `PKG-2` / Phase 9 release sign-off).
2. **`--profile full` Windows Installer Build Time in Per-Push CI:**
   - `scripts/build_exe.py` supports both `--profile full` and `--profile lite` unchanged (`D3 = A`). Per-push CI runs `--profile lite --installer` (~3 min build) to keep CI feedback under 12 minutes; `--profile full` (bundling CPU `torch==2.15.0`) uses the identical `--onedir` + Inno Setup pipeline and is exercised in release packaging (Phase 9).
3. **Downstream Phase 2–8 Domain Modules:**
   - Per strict Phase 1 scope rules, VSG (Phase 2), TCP statistical engine (Phase 3), T1–T14 test catalog (Phase 4), model store SHA-256 governance & `pickle` eradication in `store.py` (Phase 6), SQL connector secret-lifetime hardening (Phase 7), and ReportLab PDF renderer (Phase 8) were intentionally left untouched.

---

## 9. Open Risks and Their Effect on Phase 2

1. **Shared Virtual Runner Timing Variance (`Low` impact on Phase 2):**
   - Warm startup on 4-vCPU `windows-latest` (`~1.63 s`) sits slightly above the `1.50 s` `D2` physical 8-core NVMe target due to Windows Defender / cold page-table overhead on ephemeral CI VMs. Keeping heavy ML imports (`torch`, `optuna`, `statsmodels`) lazy and inside worker processes ensures Phase 2 (`VSG`) UI additions will not regress startup latency.
2. **Windows SQLite File-Handle Discipline (`Mitigated`, monitor in Phase 2):**
   - `SQLAlchemy==2.1.8` holds pooled SQLite file handles unless `reset_engine()` / `checkpoint_and_close()` or `NullPool` is used before file replacement. Phase 1 centralized this in `energybaseline/db/session.py` and `energybaseline/db/backup.py`; Phase 2 schema additions (VSG tables/migrations) will automatically inherit pre-migration online backup and validated restore.
3. **Worker Envelope Serialization Discipline for Phase 2 (`Low` impact, ready):**
   - `AnalysisPoolManager` enforces `> 1 MiB` cache staging (`{path, sha256, rows}`) and `_EB_IN_WORKER=1` SQLite isolation; Phase 2 VSG transformations running inside the pipeline must continue returning plain Python/NumPy/Pandas data structures without ORM session objects or Qt widgets.
