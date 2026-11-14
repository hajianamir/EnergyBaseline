# Phase 8 — Reporting & Audit Depth (Gate G8)

## Executor handoff status

Implementation and local gates are complete; independent reviewer acceptance is
**not** asserted. Two-OS CI disposition is recorded below in the E3 section and
`e3_ci.json`. No executor PR has been opened and main/reviewer branches are
untouched. All source work was pushed only to `arena/3611a52f-energybaseline`.

- Prerequisite: Phase 7 evidence commit `11798dd` (source `074af513e8a07f2ae3ad0aaa7d7a5b52c70a969a`).
- Phase 8 source: `95fdd4a5f82ed5186d9556fc8f91f3018c9d4f48`.
- SHA-256 of raw `git cat-file commit` output:
  `293794b90909e4c7ca9f196b234419d98a6285fec8392c77addfb148428bd8c7`.
- Source diff: **37 files changed, 2,316 insertions, 583 deletions**;
  complete file-level stat in `e1_diff_stat.txt`.
- Requirement matrix: **188/251 done**, advancing RPT-1..7 and REP-1..5.
  REP rows now carry phase 8 to match this handoff, without replacing their
  original numerical-repeatability/seed-propagation obligations.
- G-9 remains **done** and G-11 remains **in_progress**.

## Architecture and product behavior

`core/report_adapter.py` is the sole analysis-object boundary. It snapshots
JSON-native facts into immutable, content-digested `report/model.py`. The PDF,
Excel, offline HTML and Qt preview consume only this model. No analysis-class
name is present in the report package; the raw static-gate log includes that
source grep. The legacy public report APIs delegate through the same boundary.
Dashboard PDF export also uses the shared ReportLab path, not a second engine.

ReportLab Platypus handles **all PDF generation**, with both document and canvas
invariant settings, linked contents/outlines and back links. openpyxl handles
Excel, including linked section sheets and inert imported strings. HTML embeds
chart PNGs and a restrictive offline content-security policy; no browser engine
is used. Static charts use Agg and the desktop interactive preview uses QtAgg.
The preview has model-backed exact metric cells, chart selection/navigation,
static image saving and a one-click audit ZIP action.

Executive/Standard/Full share the same section grammar:
Purpose → Method → Settings → Results → Charts → Interpretation → Decision →
Caveats. All mandatory sections exist even for an empty result. Depth controls
visible detail, not the underlying evidence. Executive has an explicit page cap;
it raises rather than silently exporting an oversized summary. The real gated
sample exported **9 Executive pages**. Empty reports and all depths are tested.

Generated factual sentences and persisted disclosure annotations carry resolved
artifact IDs. Construction rejects missing references, multiple unbound sentences
and numeric claims absent from cited artifact values. Explanatory section grammar
is nonnumeric rather than metric prose. Every metric comes from a persisted
artifact; templates do not contain metric literals. All factual statements in the
mandatory-section fixture validate, and deliberately broken numeric/reference
bindings fail the build tests.

Charts carry nonempty units, source-row n, alt text and approved colors. Diagnostics
include actual/predicted, residual ACF/QQ, CUSUM, prediction intervals when bounds
exist, a persisted driver-response series, distribution and correlation views.
Okabe–Ito is used for series; correlation heatmaps use Viridis. Charts never fit a
replacement baseline. Persisted rank-one diagnostics are explicitly labeled and
are not relabeled as an overridden selection. Missing series/bounds are omitted
or disclosed rather than synthesized.

## Requirement → file → function → test

All gate-test names below are in
`tests/gates/test_phase8_reporting_and_audit.py` unless otherwise stated.
The exact node IDs are also in `docs/requirements.yaml` and the generated matrix.

| ID | File / function | Direct test evidence |
|---|---|---|
| REP-1 | `report/provenance.py:derive_seed`, `core/pipeline.py:run_analysis` | `test_seed_determinism_target_user_dataset_and_runtime`; `test_pipeline_actual_target_seed_lineage_and_repeatability` |
| REP-2 | `report/pdf.py:ReportDocument/render_pdf`; pipeline | `test_pdf_deterministic_and_empty_report_all_depths`; existing `tests/test_pipeline.py::test_determinism` |
| REP-3 | `report/provenance.py:provenance_manifest`; `run_analysis` runtime capture | `test_provenance_manifest_complete_and_no_connection_material` |
| REP-4 | `report/archive.py:export_archive`; `gui/report_preview.py:ReportPreview` | `test_archive_hashes_tables_and_renderer_agreement`; GUI one-click test |
| REP-5 | `report/archive.py:verify_archive`; `report/excel.py:table_projection` | `test_archive_hashes_tables_and_renderer_agreement`; `test_archive_malformed_and_duplicate_members_fail_closed` |
| RPT-1 | `report/pdf.py:render_pdf`; `report/template.py:section_template` | `test_renderers_twenty_metrics_links_and_page_budget` at all depths |
| RPT-2 | `report/html.py:render_html` | same test checks embedded images, alt text and resolved offline anchors |
| RPT-3 | `report/sections.py:mandatory_sections`; adapter assembly | `test_rep2_rpt3_mandatory_sections` for every mandatory section |
| RPT-4 | `report/charts.py:figure_for/render_chart/validate_chart` | `test_rpt4_chart_units_sample_size_palette_bands_and_alt_text`; correlation assertion in malformed-archive test |
| RPT-5 | `report/pdf.py:ReportDocument/render_pdf` | `test_pdf_deterministic_and_empty_report_all_depths` plus byte-repeat check on real sample |
| RPT-6 | `report/excel.py:export_companions`; `report/model.py:ReportModel` | `test_companions_and_mandatory_disclosure_banner`; `test_report_model_roundtrip_and_immutable` |
| RPT-7 | `report/banner.py:disclosure_banner`; `report/narrative.py:bind_annotation/validate_sentence` | disclosure test; `test_numeric_artifact_build_failures_and_no_template_metrics` |
| [S] diff | `report/diff.py:report_diff` | `test_report_diff_and_formula_export_preserves_live_formulas` |
| [S] formulas | `report/diff.py:export_formula_workbook` → existing integrity-checked lock exporter | same formula round-trip test |
| Preview/readback | `report/agreement.py`; `gui/report_preview.py` | `tests/gui/test_phase8_report_preview.py::test_three_renderer_agreement_twenty_metrics_and_one_click_archive` |
| Input safety | `report/safety.py`; archive bounds | `test_report_content_and_spreadsheet_injection_are_rejected_or_literal`; malformed/duplicate/tampered archive tests |
| CLI | `cli.py` report-export/audit-verify handlers | `test_cli_report_export_and_archive_verify` |

## E1 — raw command evidence

Commands ran from the repository root with the locked `.venv` Python. Raw outputs
are preserved, not replaced by this summary.

| Command | Exit/result | Raw log |
|---|---|---|
| `python -m pytest -q` | 0; **284 passed**, 582.02 s | `e1_full.txt` |
| `python -m pytest -q tests/gates` | 0; **248 passed**, 359.36 s | `e1_gates.txt` |
| `python -m pytest -q tests/gates/test_phase8_reporting_and_audit.py` | 0; **40 passed**, 40.76 s | `e1_targeted.txt` |
| Full pytest with requested coverage modules and `--cov-fail-under=60` | 0; **284 passed**, **86.52%**, 916.23 s | `e1_coverage.txt` |
| `python -m pytest -q tests/gui -p no:cacheprovider` | 0; **11 passed**, 25.71 s | `e1_gui.txt` |
| `ruff check .`; `ruff format --check .`; `pyflakes energybaseline` | 0 | `e1_static_gates.txt` |
| `guard_web_free.py`, `guard_env_readers.py`, `guard_tests_integrity.py`, `guard_no_qthread_compute.py` | all 0 | same |
| `gen_matrix.py --check` | 0; 251 IDs, 188 done | same |
| tracked-file web-stack/cache greps and forbidden report/source greps | all empty; shell asserts success | same |
| `alembic upgrade head`; `downgrade base`; `upgrade head` | all 0 | same |
| **`python -m pip-audit -r requirements.lock`** | 0; **No known vulnerabilities found** | same |
| `r-references-check` | 0 | same and `r_references_check.json` |
| `python scripts/evidence/phase8.py` | 0; real gated run + actual three-renderer agreement | `e1_evidence_generation.txt` |

Coverage includes core/models/scoring/validation/UQ/monitoring/causal/report.
The report package alone is **94%** covered (629 statements, 37 missed).

Repeated audit runs encountered a truncated Triton wheel download, then disk
exhaustion during the resolver's temporary dry-run. The failed raw logs are
retained (`e1_*network_failure.txt`, `e1_audit_disk_failure.txt`). Recovery downloaded
the **original** wheel in validated HTTP ranges, checked its SHA-256 against both
the lock and PyPI, and warmed pip's HTTP cache; see `e1_wheel_cache_recovery.txt`.
The obsolete duplicate legacy pip HTTP cache was then removed to reclaim space.
The unmodified canonical audit command subsequently returned zero twice,
including the final complete static-gate replay. No requirement, dependency,
audit rule or lock hash was relaxed.

The prompt's auditor spelling is a transparent compatibility alias invoking the
real pinned `pip_audit` module unchanged, not a mock/suppression. Both spellings'
version output is tested. pypdf is pinned and hash-locked solely for actual PDF
readback; ReportLab remains the only PDF generation engine.

Local Qt import required ABI stubs under `/tmp/qstub` because this sandbox lacks
system GL/EGL/xkb/dbus libraries. Local commands used
`LD_LIBRARY_PATH=/tmp/qstub QT_QPA_PLATFORM=offscreen`. This is disclosed, is not
physical GUI evidence, and is **not** used by CI. CI installs real OS libraries;
Ubuntu/Windows results are the independent platform evidence below.

Existing tests were not modified/deleted. Only new Phase 8 fixtures/gate/GUI tests
were added. Frozen baseline and LICENSE hashes remain unchanged:

- LICENSE: `1c633e61e328b34b883571ae25a92cf6a390e6f2752871331545d70a5b553afe`.
- Frozen test manifest: `6f3abde78940ac71c104a32305c7a69bc2a9c90a09f6660a332de0eac467f3dc`.

## E2 — persisted product evidence and integrity

`scripts/evidence/phase8.py` runs the **actual** monthly-building sample through
accepted VSG and locked TCP, then the production pipeline/adapter/renderers and
real Qt metric model. No mocked inference result substitutes for this evidence.

- `system_health.json`, `sbom.json`: actual product health and component inventory.
- `report-model.json`: immutable report single source; report ID
  `d6a7f8786e05adb1c04a74400322edae4240f88eb46a1a9f94201d7d6dd233c5`.
- `provenance.json`: dataset SHA, target seed lineage, runtime versions, CPU/OS,
  configuration and source commit, captured at analysis rather than recaptured
  from a later export process. Legacy runs explicitly disclose missing original
  runtime capture instead of fabricating it.
- `report.pdf`, `report.xlsx`, `report.html`, `metrics.csv`, `tables.json`: actual
  exports. All **20 key metrics** agree across PDF/Excel/Qt model readback;
  `renderer_agreement.json` records model values, page count and byte determinism.
  This production sample has **18 finite values and 2 explicitly unavailable
  AIC/BIC values**; null availability agrees too. Separately, the fitted synthetic
  OLS fixture exercises **20 finite metrics** through actual PDF, Excel and Qt
  render/readback in the gate/GUI tests; it is not substituted for the gated
  production-pipeline sample.
- `audit.zip`: SHA-256
  `cbb591b8ff40e84ff8a019ea48ea9c5c01315be167bdb651669e3417c55fd33d`.
  `audit_archive.json` records **8 members verified**, **28 tables reproduced**,
  **20 metrics reproduced**. Model state includes AUTO decisions/TestResult data;
  config and provenance have separate bound members.
- `manifest.json` hashes the evidence files; `e1_source_binding.json` independently
  binds the source commit object. The manifest itself is not self-hashed.

Verification never extracts the archive. It rejects traversal, duplicates,
symlinks, member-set changes, model/table/provenance mismatch and oversized ZIPs.
It reads actual PDF/Excel/HTML/CSV metric values, including after a malicious
member edit with an updated local member hash. An expected external archive digest
can be supplied for adversarial replacement protection. This is **integrity and
persisted-table reproduction**, not a signature or re-execution of model training.

## E3 — actual platform CI

Run: https://github.com/hajianamir/EnergyBaseline/actions/runs/43577590362

Source: `95fdd4a5f82ed5186d9556fc8f91f3018c9d4f48`.
Both Ubuntu and Windows jobs **completed successfully**. Disposition and per-job
timings are copied directly from GitHub job records into `e3_ci.json`. Both jobs include the G8
report generation/readback step, all gates, full coverage, real-Qt GUI tests,
lockfile installation, pip-audit and SBOM. See the final platform table below.


### Final platform results

| Platform | Conclusion | Started (UTC) | Completed (UTC) | Job duration | G8 step |
|---|---|---|---|---|---|
| Phase 8 Gate G8 (ubuntu-latest) | **success** | 2026-11-14T08:49:03Z | 2026-11-14T09:17:04Z | 28m 1s | 2m 9s |
| Phase 8 Gate G8 (windows-latest) | **success** | 2026-11-14T08:49:03Z | 2026-11-14T09:41:54Z | 52m 51s | 2m 47s |

### CI log access limitation

The GitHub REST job records are preserved verbatim. Downloading raw CI job logs
redirects to an Azure blob host blocked by this sandbox's outbound-host policy
(the attempt returned EOF), so no empty/placeholder CI log is presented as
successful evidence. Full logs and uploaded CI artifacts remain available at the
run URL to the independent reviewer. Local raw E1 logs are retained here.

## Deviations, limitations and residual risks

See `docs/deviations.md` DEV-12..16 for implementation deltas. Important limits:

1. The requirement matrix's REP-2 is numerical repeatability, not only section
   completeness. Existing strict same-environment repeatability remains tested;
   PDF byte determinism is checked separately. Source/environment provenance
   deliberately changes report identity when its inputs change.
2. Excel's native numeric cells have finite decimal precision. Readback uses
   relative **and** absolute 1e-12 tolerance; the physical fixture passed absolute
   agreement as well. The real sample's largest Excel absolute round-trip delta
   was 3.637978807091713e-12 for a large-valued actual mean, well within relative
   tolerance. PDF numeric rows preserve the full source decimal representation.
   This is not a claim of absolute 1e-12 Excel agreement at arbitrary magnitudes.
3. The Executive cap is enforced, not a promise that arbitrary multi-target input
   always fits. Very wide/large tables should use Full Excel/JSON; Excel cell-size
   overflow fails explicitly. Atlas scope is persisted diagnostics, not a newly
   inferred exhaustive multivariate analysis during export.
4. Chart n is the persisted source-row count, not a claim that every row survives
   every per-chart finite-value filter. Interval bands require actual persisted
   bounds. Missing data is unavailable, never invented. Persisted selected-series
   diagnostics are needed to replace the explicitly labeled rank-one defaults.
5. Numeric-reference validation proves source binding/value agreement, not that a
   quoted source annotation is scientifically true. Explanatory grammar text is
   distinct from artifact-bound factual narrative. Audit hashes are not signatures.
6. **G-11 is still in_progress** despite this clean locked audit/SBOM. No new claim
   is made about deployment signing, package release or supply-chain lifecycle.
7. Phase 7 physical Win11 D2/human screenshot acceptance remains unexecuted.
   CI/offscreen Qt is not physical installer/human review evidence.
8. Conformal intervals do not acquire distribution-free guarantees for arbitrary
   autocorrelated/regime-shifted series. Seasonal false-drift and regime-shift risks
   remain; drift is advisory only and never mutates/refits a locked baseline.
9. R reference freshness remains time-bounded: the check passes in this evidence,
   but refresh is required before the recorded 2026-11-17 deadline. The optional
   DoWhy/SciPy conflict remains disclosed; no forced incompatible install occurred.
10. Packaging/installer/signing acceptance and portfolio GUI work are outside
    Phase 8. No existing frozen tests, reviewer branch or main content were changed.
