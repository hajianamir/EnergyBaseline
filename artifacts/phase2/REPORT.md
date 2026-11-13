# Phase 2 Verification & Gate G2 Report — Data / Time / Column Setup

- **Branch:** `arena/3611a52f-energybaseline`
- **Base Commit:** `893d2de6064ffdc3db50081c5eb4c9216f1fdf75` (Phase 1 Gate G1 verified)
- **Date:** `2026-11-12`
- **Scope:** `docs/EXECUTION_PLAN.md` §Phase 2 (`DAT-1..DAT-8`, `AC-06`, `AC-28`, Gate G2)

---

## 1. Delivered Architecture & Functional Summary

| Capability | Implementation | Verification |
|---|---|---|
| **Modelled Column Schema (`DAT-4`, `DAT-7`, `DAT-8`)** | `energybaseline/core/schema.py` defines `ColumnRole` (`time`, `target`, `predictor`, `static_factor`, `group`, `weight`, `ignore`), `QuantityType` (`flow`, `stock`, `rate`, `quality`, `index`), `Inclusion` (`known_in_advance`, `observed_only`), `Availability`, `OutlierActionPolicy` (`flag_only`, `ask_user`, `auto_apply`), `ColumnSpec`, `NonRoutineEventSpec`, and `DatasetSchema` with SHA-256 canonical JSON envelopes and strict structural invariants (exactly 1 `time` column, `>=1` `target` column, valid IANA timezone, DataFrame column presence). | `tests/gates/test_phase2_schema_and_db.py` |
| **DB Persistence & Reversible Migration `0002` (`DAT-5`, `DAT-8`)** | `alembic/versions/0002_phase2_schema_and_nre.py` adds `dataset_schemas` and `non_routine_events` (`0001 <-> 0002` reversible). `energybaseline/core/store.py` persists and verifies SHA-256 integrity on load (`SchemaIntegrityError` on tamper). | `tests/gates/test_phase2_schema_and_db.py`, `artifacts/phase2/alembic_ladder.txt` |
| **Quantity-Type Aggregation & Disaggregation Refusal (`DAT-3`)** | `energybaseline/core/temporal.py` dispatches resampling through an immutable `MappingProxyType` (`_AGGREGATION_DISPATCH`, zero `getattr`): `flow -> sum`, `stock -> last`, `rate -> time_weighted` (`∫ x dt / Δt` in nanoseconds), `quality -> mean/median`, `index -> mean`. Name-based regex is demoted to an audited heuristic suggestion (`suggested_by_heuristic=True`). Coarser-than-declared inputs raise `DataRefusedError` (`code="DATA_REFUSED"`). | `tests/gates/test_phase2_temporal_and_dst.py` |
| **4-Zone DST Engine & Gap Classification (`DAT-2`, `DAT-3`, `E1`)** | `normalize_timestamps_utc` + PEP-495 `fold` disambiguation stores monotonic UTC timestamps internally while preserving local calendar day aggregation (`23h`/`25h` and `23.5h`/`24.5h` transition days). `classify_gaps` partitions missing intervals into `short_gap`, `medium_gap`, and `extended_shutdown`. | `tests/gates/test_phase2_temporal_and_dst.py`, `artifacts/phase2/evidence/e1_dst_property_report.json` |
| **Streamed Ingest, Unit Registry & Verified Parquet Cache (`DAT-1`, `DAT-6`, `DAT-8`)** | `energybaseline/core/ingest.py` streams CSV/TSV in 50k-row chunks with BOM/UTF-8/CP1252 encoding detection, delimiter & European decimal (`1.234,56`) auto-detection, multi-sheet Excel selection, direct `.parquet` loading via `pyarrow==24.0.0`, SHA-256-verified Snappy Parquet sidecar caching (purging tampered cache/manifest files), and an immutable `UNIT_REGISTRY` (`kWh`, `kW`, `degC`, `m3`, `kg`). | `tests/gates/test_phase2_ingest_and_sql.py` |
| **Read-Only External SQL AST & Driver Guard (`AC-06`, `DAT-7`)** | `energybaseline/core/sqlsource.py` parses SQL with `sqlglot` AST (`validate_select`), rejecting multi-statement queries, DDL/DML, DML-in-CTEs, `SELECT INTO`, `FOR UPDATE`, `PRAGMA`/`ATTACH`/`COPY`, and dangerous functions, backed by C-driver read-only connections (SQLite `as_uri() + "?mode=ro"` + `PRAGMA query_only = ON`, PostgreSQL `default_transaction_read_only=on`, MySQL `READ ONLY`, SQL Server `ApplicationIntent=ReadOnly`), `timeout_seconds=10`, and bounded `fetchmany(max_rows)`. | `tests/gates/test_phase2_ingest_and_sql.py` |
| **No-Fabrication Missing Policy, NREs & Outlier Action Policy (`DAT-5`)** | `energybaseline/core/quality.py` enforces `verify_no_target_fabrication` (`FabricationError` if any missing target observation is imputed or rows are fabricated), leak-free training-partition-only predictor imputation (`~eval_mask`, never crossing `extended_shutdown`), Non-Routine Event `exclude`/`flag`/`adjust_indicator` handling, and explicit `OutlierActionPolicy` (`flag_only`, `ask_user`, `auto_apply`) recording full training-set deltas. | `tests/gates/test_phase2_quality_and_cli.py` |
| **CLI & Desktop Setup Schema Editor (`DAT-4`, `DAT-6`, `E2`)** | `energybaseline/cli.py` (`schema-suggest`, `schema-edit`, `schema-validate`, `temporal-preview`, also exposed via `launcher.py cli`) and `energybaseline/gui/pages_setup.py` (`Column Metadata (Schema)` tab, `OutlierActionPolicy` selector, SHA-256 JSON import/export, SQLite persistence, zero `QThread`). | `tests/gates/test_phase2_quality_and_cli.py`, `tests/gui/test_phase2_gui_schema.py` |

---

## 2. Gate G2 & Evidence Summary

| Check | Result | Artifact |
|---|---|---|
| `ruff check .` & `ruff format --check .` | **PASS** (0 lint findings, 0 format diffs) | `artifacts/phase2/gates_g2.txt` |
| `python -m pyflakes energybaseline` | **PASS** (0 warnings) | `artifacts/phase2/gates_g2.txt` |
| Architecture & baseline guards (`web_free`, `env_readers`, `tests_integrity`, `no_qthread_compute`) | **PASS** | `artifacts/phase2/gates_g2.txt` |
| Traceability matrix (`python scripts/gen_matrix.py --check`) | **PASS** (`249` IDs verified, `51` `done` including `DAT-1..8`, `AC-06`, `AC-28`) | `artifacts/phase2/gates_g2.txt` |
| Alembic reversible migration cycle (`upgrade head -> downgrade base -> upgrade head`) | **PASS** (`0002 (head)`) | `artifacts/phase2/alembic_ladder.txt` |
| Full test suite + `energybaseline/core` coverage | **PASS** (`123 passed`, **`80.03%`** core coverage, gate `>= 75%`) | `artifacts/phase2/pytest_full.txt` |
| Headless GUI suite + `energybaseline/gui` coverage | **PASS** (`4 passed`, **`50.92%`** GUI coverage, gate `>= 48%`) | `artifacts/phase2/pytest_gui.txt` |
| Supply-chain audit (`pip-audit -r requirements.lock` + CycloneDX SBOM) | **PASS** (`0` vulnerabilities across 166 components incl. `pyarrow==24.0.0`) | `artifacts/phase2/sbom_and_audit.txt` |
| **Evidence E1** — 4-Zone DST Property & Conservation JSON (`Europe/Berlin`, `America/New_York`, `Australia/Lord_Howe`, `UTC`) | **PASS** (`sha256: 09e0eaaec6987d2dadd3e19b59f7c870e9b594272ab4568b20227fc447af1bec`) | `artifacts/phase2/evidence/e1_dst_property_report.json` |
| **Evidence E2** — CLI Schema & Temporal Preview JSON | **PASS** (`sha256: b1ed5e1d2f7997b8e6918e90df55d02f0eb8ec70bff45061428f21c7a2a2f0a0`) | `artifacts/phase2/evidence/e2_cli_temporal_preview.json` |
