# EnergyBaseline v6 — Phase 8 Executor Prompt: Reporting & Audit Depth (Gate G8)

You are the **executor**, not the judge. An independent reviewer reads your code, re-runs your commands
personally, and votes PASS / PASS-WITH-CONDITIONS / FAIL. You own only `arena/3611a52f-energybaseline`.

## 1. Phase ID and scope

**Phase ID:** `Phase 8 — Reporting & Audit Depth`
**Depends on:** Phase 7 (Gate G7 PASS, merged into `arena/af928d3c-energybaseline` → `arena/2124dfe7-energybaseline` @ `11798dd`)
**Branch:** `arena/3611a52f-energybaseline` (sole push target; `main` and `arena/af928d3c-energybaseline` untouched;
linear history, zero force-push, no executor PR into `main`).
**Estimate:** 6–8 engineer-weeks.

Scope per `docs/EXECUTION_PLAN.md` Phase 8: §23 (REP-1..5, RPT-1..7), G-9, G-11, AC-17, AC-28.
Per `docs/requirements_matrix.md`: RPT-1..7 are `planned` for Phase 8; REP-1..5 are `planned` (Phase 7 in matrix,
§23 in execution plan — implement as scoped); G-9 (done Phase 6) and G-11 (in-progress Phase 0) must be preserved.

## 2. Branch protocol (non-negotiable)

- Work only on `arena/3611a52f-energybaseline`; never the reviewer's branch, never `main`.
- No force-push.
- No executor PR into `main`.
- The reviewer fetches your branch read-only, runs the gate block **personally**, and votes.
- Docs English-only (G-7); Persian only for chat with the owner.

## 3. Deliverables (ID → file/function → test)

| Requirement / Work ID | File & Function / Class | Verification Test(s) / Gate(s) |
|---|---|---|
| One JSON report model | `energybaseline/report/model.py` — `ReportModel` as single source for in-app, PDF, Excel | report-model round-trip test |
| PDF renderer | `energybaseline/report/pdf.py` — ReportLab Platypus only (D5=A; zero GTK/WeasyPrint) | pdf-render test; no-weasyprint guard |
| Excel renderer | `energybaseline/report/excel.py` — openpyxl export | excel-export test |
| In-app preview | `energybaseline/gui/report_preview.py` — matplotlib Agg static + QtAgg interactive, no QWebEngineView | preview-render test; no-qwebengine guard |
| REP-1 | `energybaseline/report/provenance.py` — deterministic seed derivation from dataset SHA-256 + target + user seed | seed-determinism test |
| REP-2 | `energybaseline/report/sections.py` — EDA Atlas, Correlation Atlas, Full Test Results Appendix, Decision-Sensitivity Summary, Rejected/Not-Shortlisted registers, AUTO log, model cards, drift, reproducibility, glossary, audit appendix | section-content test per REP-2-ID |
| REP-3 | `energybaseline/report/provenance.py` — per-run manifest: package versions, OS, CPU count, dataset SHA-256, config JSON, commit SHA | provenance-manifest test |
| REP-4 | `energybaseline/report/archive.py` — one-click audit archive export (.zip) | zip-archive test |
| REP-5 | `energybaseline/report/archive.py` — audit archive verifier re-validating hashes and reproducing tables | archive-verifier test |
| RPT-1 | `energybaseline/report/template.py` — per-section template Purpose→Method→Settings→Results→Charts→Interpretation→Decision→Caveats | template-structure test |
| RPT-2 | `energybaseline/report/html.py` — self-contained single-file offline HTML with embedded base64/SVG, zero CDN | html-export test |
| RPT-3 | `energybaseline/report/sections.py` — all mandatory sections present | mandatory-sections test |
| RPT-4 | `energybaseline/report/charts.py` — matplotlib charts: units, n, CI bands, colour-blind-safe palette (Okabe-Ito/Viridis), alt text | chart-standards test |
| RPT-5 | `energybaseline/report/pdf.py` — deterministic PDF (fixed ReportLab timestamp/ID metadata) | deterministic-pdf test |
| RPT-6 | `energybaseline/report/excel.py` — CSV/Excel/JSON companion export | companion-export test |
| RPT-7 | `energybaseline/report/banner.py` — disclosure banner: manual overrides, skipped families, assumption fallbacks | disclosure-banner test |
| Three depth levels | `energybaseline/report/depth.py` — Executive / Standard / Full with linked TOCs | depth-levels test; exec-depth <= 10 pages |
| Artifact-ID narrative | `energybaseline/report/narrative.py` — every numeric sentence carries an artifact ID; build-failing number↔artifact test | id-coverage test; build-fail test |
| Report diff / Excel formula | `energybaseline/report/diff.py` — report diff + Excel-formula export as [S] | diff-test; formula-export test |
| G-9 | `energybaseline/lock/baseline_lock.py`, `core/store.py` — safe serialization only (JSON, SQLite, joblib + SHA-256; no pickle) | safe-serialization test (regression) |
| G-11 | `requirements.lock`, `.github/workflows/ci.yml` — exact pins, SHA-256 lockfile, pip-audit, CycloneDX SBOM | lockfile-pins test; pip-audit test |

## 4. Gate G8 command block (canonical — run every command; every command must exit 0)

Every command below must exit 0. The `test -z "$(…)"` lines assert empty output on purpose: a bare `grep`
exits 1 on a clean tree, which would contradict "must exit 0".

```bash
python -m pytest -q
python -m pytest -q tests/gates
python -m ruff check . && python -m ruff format --check .
python -m pyflakes energybaseline
python scripts/gates/guard_web_free.py
python scripts/gates/guard_env_readers.py
python scripts/gates/guard_tests_integrity.py
python scripts/gates/guard_no_qthread_compute.py
python scripts/gen_matrix.py --check
python -m pytest -q --cov=energybaseline/core --cov=energybaseline/models --cov=energybaseline/scoring --cov=energybaseline/validation --cov=energybaseline/uq --cov=energybaseline/monitoring --cov=energybaseline/causal --cov=energybaseline/report --cov-fail-under=60
python -m pytest -q tests/gui -p no:cacheprovider
test -z "$(git ls-files | grep -Ei 'package.json|tsconfig|\.tsx?$|next\.config|drizzle|eslint|postcss')"
test -z "$(git ls-files | grep -E '__pycache__|\.pyc$')"
test -z "$(grep -rn 'weasyprint' energybaseline scripts requirements.txt requirements.in requirements.lock)"
test -z "$(grep -rn 'os.environ\\[\\\"DATABASE_URL\\\"\\]' energybaseline/launcher.py)"
test -z "$(grep -rn 'pickle.load' energybaseline/)"
test -z "$(grep -rn 'from flask\\|from fastapi\\|import uvicorn' energybaseline/)"
test -z "$(grep -rn 'QWebEngineView' energybaseline/)"
test -z "$(grep -rn 'RunResult' energybaseline/report/)"
test -z "$(grep -rn 'hardcoded' energybaseline/report/template.py energybaseline/report/narrative.py)"
python -m alembic upgrade head
python -m alembic downgrade base
python -m alembic upgrade head
python -m pip-audit -r requirements.lock
```

## 5. Gate G8 specific rejection criteria (executor must satisfy these)

1. **Numerical agreement across the three renderers** — PDF, Excel, and in-app preview must agree on 20 key metrics.
2. **Empty-data report still renders** — a report with no data must not crash.
3. **Alt-text and TOC tests** — every chart has alt text; every depth level has a linked TOC.
4. **`"no hardcoded metric in templates"`** — no literal metric values in report templates.
5. **100% of numeric sentences carry IDs** — every numeric sentence in the narrative carries an artifact ID.

**Rejected if:**
- any renderer reads `RunResult` directly;
- a narrative sentence lacks an artifact ID;
- WeasyPrint or Qt produces the PDF (ReportLab only);
- the Executive depth exceeds 10 pages.

**Auto-fail triggers:**
- A renderer ships without a numerical-agreement test.
- Editing or deleting an existing test to go green = instant FAIL.
- Running inferential tests before VSG/TCP gate passes.
- WeasyPrint or Qt used for PDF generation.

## 6. Evidence requirements (E1/E2/E3 per DECISIONS.md D2 = B+A)

- **E1:** raw command output in `artifacts/phase8/*.txt` + commit SHA-256
- **E2:** machine-readable JSON produced by the product itself (system.health, system.sbom, report-model JSON, provenance manifest, audit archive JSON)
- **E3:** CI on `windows-latest` + `ubuntu-latest` (D2 physical reference hardware benchmarks are non-normative in this sandbox)

Write `artifacts/phase8/REPORT.md` with: Phase ID, full commit SHA, `git diff --stat`, ID→file→function→test table,
commands executed + raw output paths, pytest summary + coverage, artifacts + SHA-256, E3 logs/timings, deltas in
deviations/assumptions/not_implemented, what was not proven and why, open risks.

## 7. What is intentionally NOT in Phase 8 scope

- `.ebproj` ZIP packaging / NSIS-Inno installer — **Phase 9 (PKG-1..4)**
- Deterministic reproducible build / Authenticode signing — **Phase 9 per D1=B**
- Physical Windows 11 23H2 reference-machine benchmarks — **Phase 7/9 per D2 (non-normative CI)**
- Multi-target / portfolio rollup GUI — **Phase 7/9 (MT-1..5)**

## 8. Leftovers carried from Phase 7 (mention in your report)

- Normative D2 physical reference hardware benchmarks (`8-core · 16 GB · NVMe · Win11 23H2 · 1920×1080@100%`) and
  human visual screenshot inspection were **not** executed; Windows E3 evidence remains CI non-normative.
- Phase 7 coverage: monitor `energybaseline/uq` + `monitoring` + `causal` (92%) for regression.
- D4 R reference validation: nightly R job evidence expires after 14 days without a run — ensure
  `tests_catalog/r_references/` JSON is versioned and current (< 14 days).
- Phase 7 §9 open risks: conformal calibration under regime shifts (UQ-1/UQ-3), drift detector false positives
  under seasonal loading (DR-1..3), 14-day R reference freshness (TC-8 / CI-3, refresh before `2026-11-17`).

## 9. Open risks and their effect on Phase 8 → Phase 9

1. **ReportLab deterministic metadata (RPT-5):** fixed ReportLab creation timestamp/ID metadata must be stable
   across runs for regression testing; verify no wall-clock or random IDs leak into PDF bytes.
2. **Colour-blind-safe palettes (RPT-4):** Okabe-Ito / Viridis palettes must be verified programmatically, not
   assumed — chart standards test must assert palette membership.
3. **Three depth levels and linked TOCs:** Executive depth must not exceed 10 pages; TOC links must resolve
   correctly across PDF, HTML, and Excel renderers.
4. **14-day R reference rolling freshness (TC-8 / CI-3):** committed R reference JSONs must be refreshed by
   `.github/workflows/r-reference.yml` before staleness hard-fails `tests/test_reference_staleness.py`.

## 10. What the reviewer rejects (auto-fail triggers)

- Test claims without raw output; mock-centric tests standing in for `[M]` IDs; test tampering.
- Hardcoded metrics in report templates; Windows claims without E3; adding a web stack.
- Silent data mutation; dynamic `eval`/`getattr` in the dispatcher; loading pickle without SHA-256.
- Editing or deleting an existing test to go green = instant FAIL.
- Running inferential tests before VSG/TCP gate passes.
- Any renderer reading `RunResult` directly; narrative sentence without artifact ID.
- WeasyPrint or Qt producing the PDF; Executive depth exceeding 10 pages.
- Stale R reference JSON (>14 days without nightly run).

## 11. Definition of Done

`pytest -q` green · `pyflakes` clean · `requirements_matrix.md` updated (no `done` row without tests;
RPT-1..7, REP-1..5 advanced as appropriate; G-9 stays `done`, G-11 stays `in-progress`) · `deviations.md`
updated · a runnable artifact (not a mock) · no regression in earlier phase tests · every `[M]` ID has an
automated test or documented manual check **with an evidence file** · numerical agreement across three renderers
for 20 key metrics · empty-data report renders · alt-text + TOC tests pass · no hardcoded metric in templates ·
100% of numeric sentences carry artifact IDs · ReportLab-only PDF (no WeasyPrint/Qt) · Executive depth ≤ 10 pages ·
R reference JSON current (<14 days) · Gate G8 criteria all satisfied.

---

Language compliance (G-7): 100% English across all code, docstrings, comments, PowerShell scripts, tests,
workflows, commit messages, and reports.

Licence: Proprietary / Internal (`LICENSE` committed; do not modify). `artifacts/review_baseline/` must not be
modified or deleted.
