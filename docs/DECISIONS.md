# EnergyBaseline v6 — Decision Record (blocking decisions)

**Status:** ✅ **RATIFIED — all decisions closed (2026-11-12)**
**Decider:** product owner · **Author:** reviewer · **Branch:** `arena/af928d3c-energybaseline`
**Owner vote:** D1=B · D2=B+A · D3=A · D4=B · D5=A · S7=Proprietary — all match the reviewer's recommendation.

> Any decision still `OPEN` blocks the dependent phase. If the owner does not vote, the **silence default**
> applies and is recorded in `docs/deviations.md`. This file is the only authoritative record of the
> decisions (G-7: the repository ships English only).

## Summary of ratified decisions

| # | Topic | Ratified | Consequence |
|---|---|---|---|
| **D1** | Authenticode signing | **B** — targeted deferral + ready pipeline | `PKG-3 → [S]`, dated trigger before any external distribution |
| **D2** | Windows evidence machine | **B+A** — mandatory CI + one physical reference machine | normative numbers only from the reference hardware |
| **D3** | NN stack packaging | **A** — Full by default + optional Lite | AC-10 proven on Full; Lite carries a known limitation |
| **D4** | R reference validation | **B** — versioned JSON + nightly R job | stale-reference detection is a build gate |
| **D5** | PDF engine | **A** — ReportLab only | WeasyPrint banned repository-wide by an AST gate |
| **S7** | License | **Proprietary / Internal** | `LICENSE` + `THIRD_PARTY_NOTICES.md` in every build |

Estimated effect of the bundle: ≈ 3 extra days in Phases 0–1 for groundwork, versus 2–3 weeks lost to
procuring a certificate and an HSM before any code exists.

---

# D1 — Authenticode code signing

**Relevant:** PKG-3, AC-20, §25 · **Phases:** 0 (decision), 9 (execution)

### Context
The prompt marks code signing `[M]` and requires private keys to live only in CI secrets. In practice
(since the CA/Browser Forum hardware requirement), a code-signing private key must be held on a
**FIPS 140-2 Level 2 token or an HSM**; a `.pfx` in CI secrets is no longer acceptable. Signing therefore
means: certificate + (physical token **or** a cloud HSM service) + identity validation.

### Options

| Option | What it gives | Cost | Main risk | Compliance |
|---|---|---|---|---|
| **A — OV/EV certificate + token/HSM** | Full PKG-3; clean SmartScreen reputation over time | OV ≈ $200–500/yr, EV ≈ $300–700/yr, token ≈ $50–150 (or cloud HSM) | A hardware token is practically unusable on hosted runners ⇒ cloud HSM migration | PKG-3 ✅ |
| **B — Defer + ready pipeline (chosen)** | Unsigned release with checksums and an “Unknown publisher” guide; the signing script is written and tested from day one with a self-signed cert | $0 | SmartScreen/AV reputation; users click “More info → Run anyway” once | PKG-3 → **[S]** + deviations entry + signed risk acceptance |
| **C — Cloud HSM (Azure Trusted Signing / DigiCert KeyLocker)** | Signing without a physical token, automatable | ≈ $10/mo (Azure) up to a few hundred/yr | Eligibility checks, approval latency, cloud dependency in the build path | PKG-3 ✅ (with a “signed via service” exception) |
| **D — Permanent unsigned** | Simplest | $0 | AV/EDR quarantine in managed environments; more support | PKG-3 removed from the matrix |

### ✅ Owner vote: **B**

**Effects folded into the plan**
- `PKG-3` degrades to **`[S]`**; `docs/deviations.md` carries a mandatory **trigger** field.
- Phase 0: `scripts/sign/sign_artifacts.ps1` + a CI test with a self-signed certificate
  (`signtool sign` → `signtool verify /pa`).
- Phase 9: the release bundle ships `SHA256SUMS.txt` and an “Unknown publisher” section in `USER_GUIDE.md`.
- **Trigger:** “before any distribution outside developer machines, D1 must return to A or C.”
  If the trigger is ignored, G9 is not closed.

### Why not the others
A/C require procurement and identity work that would block Phase 0 for weeks, with no effect on code
quality. D removes a `[M]` requirement permanently and increases support load.

**Silence default:** B.

---

# D2 — Reference machine for Windows (E3) evidence

**Relevant:** LIF-1/2, SCR-1/6, PKG-1..6, AC-03, AC-19, native UX-* · **Phases:** 1, 9

### Context
Many `[M]` IDs are provable **only** on real Windows: Job Object and zero orphans, warm start, per-user
install, `.ebproj` association, taskbar progress, tray notifications, High-DPI, SmartScreen. The question
is **where** and **on what hardware** the evidence is produced.

### Options

| Option | What it gives | Cost/time | Risk | Sufficient for |
|---|---|---|---|---|
| **A — GitHub Actions `windows-latest` only** | Automatic, repeatable on every PR; `pytest-qt` runs | Free for public repos; 2× minutes for private | 4 vCPU/16 GB/SSD ⇒ timings are meaningless; no manual Explorer/DPI/SmartScreen checks | Windows logic tests (Job Object, install, migration) |
| **B — One physical reference machine (Win11 x64)** | Real numbers and real Explorer/DPI/Taskbar behaviour | $0 (existing machine) + 4–8 h of evidence scripting | Manual and less repeatable ⇒ needs a documented ceremony | all of E3 |
| **C — Windows VM** | Cheap, snapshot-friendly | 1 day setup | Distorted timings (warm start becomes non-citable), software GPU, unreliable DPI | install/association/migration — **not** performance |
| **D — Physical only** | Strictest | No CI; manual per PR | Regressions found late | everything, slowly |

### ✅ Owner vote: **B + A**

**Effects folded into the plan**
- **Normative reference hardware (recorded in `docs/SUPPORT_MATRIX.md`):**
  `8-core x64 · 16 GB RAM · NVMe SSD · Windows 11 23H2 x64 · integrated GPU · 1920×1080 @100 %`.
- Normative SCR-6 numbers come only from that machine; `windows-latest` numbers are reported separately and
  labelled “CI, non-normative”.
- Phase 1: `scripts/evidence/{lif_orphans,startup_time,installer_smoke,dpi_matrix}.ps1`, each emitting JSON.
- Physical evidence is regenerated **per release** (not per commit) and stored in `artifacts/phaseX/win/` with SHA-256.

### Why not the others
A alone cannot support warm-start or SmartScreen/DPI claims (the reviewer would grant PASS-with-conditions at
best). C distorts exactly the numbers the prompt asks to publish.

**Silence default:** B+A.

---

# D3 — Neural-network stack: base installer or optional component?

**Relevant:** §17.1 portfolio, AC-10, SCR-5/6, PKG-1 · **Phases:** 0 (dependencies), 1 (installer), 6 (modelling)

### Context
AC-10 `[M]` requires an NN in the portfolio and at least one **sequence NN** (LSTM/GRU/TCN). SCR-6 says to
measure size/startup and, if the budget is exceeded, make the NN stack optional. torch is large: the
CPU wheel download is several hundred MB and the installed onedir footprint is roughly **1.5–2.5 GB**
(this must be **measured** in M-1; no final number is claimed here).

### Options

| Option | Approx. size | Advantage | Drawback | AC-10 |
|---|---|---|---|---|
| **A — Full default + Lite optional (chosen)** | Full: GBs / Lite: light | Default install satisfies every AC; Lite serves weak machines | Two build profiles, two test paths, more docs | ✅ on default |
| **B — Single installer with torch** | Large | Simplest | Slow install, AV friction, wasted space for users who never use NNs | ✅ |
| **C — Light base + offline NN add-on** | Light base | Small base installer | On a base machine without the add-on, AC-10 **fails**; needs unlock logic and messaging | ⚠️ conditional |
| **D — Lite only (drop sequence NNs)** | Light | — | Explicit violation of AC-10 | ❌ |

### ✅ Owner vote: **A**

**Effects folded into the plan**
- Default **Full** so acceptance never collapses on “add-on not installed”.
- **Lite** is the same code with `build_exe.py --profile lite` (no torch); it shows a banner:
  “Sequence NNs unavailable in this build — install the Full build”.
- Both builds keep the scikit-learn **MLP**, so Lite still has an NN family; AC-10 evidence is taken on Full.
- The **CPU-only torch wheel is pinned** and the wheel/platform is recorded in `docs/SUPPORT_MATRIX.md`.

### Why not the others
C turns a `[M]` requirement into a conditional one. D violates AC-10 outright.

**Silence default:** A.

---

# D4 — How R reference validation runs in CI

**Relevant:** §27 (R reference), AC-21, AC-25 · **Phase:** 4

### Context
Every custom statistical test needs an R script and a tolerance test (Chow, Quandt–Andrews, Pettitt,
Diebold–Mariano, Nemenyi, Little's MCAR, MCS). Installing R plus
`strucchange`, `MissMech`, `tseries`, `urca`, `forecast`, `Kendall` on every PR is slow and brittle.

### Options

| Option | What it gives | Cost/time | Risk | Proof quality |
|---|---|---|---|---|
| **A — R on every PR** | Always fresh | +2–5 min per PR, heavy packages, weak caching | CRAN/compile flakiness | strongest |
| **B — Versioned JSON + nightly R job (chosen)** | Fast, deterministic PRs; auditable reference | 0 in PRs; one nightly job | A sleeping nightly job makes JSON stale | strong, with guards |
| **C — Pinned Docker image with R** | High reproducibility | 1 day setup + image size | Needs registry access in CI | strong |
| **D — Manual generation + tolerance test** | Simple | 0 | Cannot prove the JSON came from that script | weak |

### ✅ Owner vote: **B** (with three guards)

**Effects folded into the plan**
- `tests/reference/<test>.R` + `<test>.json` with
  `{R_version, packages, seed, n, date, r_script_sha256, values…}`.
- `tests/test_reference_staleness.py`: if the R script hash no longer matches the JSON ⇒ **build fails**.
- `.github/workflows/r-reference.yml`: nightly + `workflow_dispatch`; on divergence beyond tolerance it
  **opens an issue** automatically.
- **Reviewer rule:** if that job has not run for 14 days, the evidence counts as **expired** and G4 is not closed.

**Silence default:** B.

---

# D5 — PDF engine

**Relevant:** REP-1/REP-3, AC-17, AC-28 · **Phases:** 0 (dependencies), 8 (reporting)

### Context
REP-3 is explicit: “PDF via **ReportLab** (pure-Python, no GTK/WeasyPrint, no Qt dependency)”. The current
code does the opposite: `requirements.txt` declares WeasyPrint and `core/report.py` renders Jinja/HTML to PDF;
WeasyPrint needs GTK/Pango, which is not advisable on a clean Windows target.

### Options

| Option | Compliance | Typography | Risk |
|---|---|---|---|
| **A — ReportLab only (chosen)** | ✅ exactly REP-3 | Good (`platypus`: linked TOC, tables, headers/footers, page numbers) | Styles must be written from scratch |
| **B — ReportLab + optional WeasyPrint** | ❌ REP-3 | Excellent (CSS) | Two render paths ⇒ numerical divergence; GTK on Windows |
| **C — ReportLab + Qt text PDF** | ❌ (“no Qt dependency”) | Moderate | Preview ≠ final PDF |

### ✅ Owner vote: **A**

**Effects folded into the plan**
- `weasyprint` removed from `requirements.txt`; `reportlab==5.1.0` added and pinned.
- All 10 current WeasyPrint references removed (including `core/report.py`, `README.md`,
  `docs/USER_GUIDE.md`, `scripts/build_exe.py`).
- `scripts/gates/guard_web_free.py` rejects the token `weasyprint` anywhere in executable code and manifests.
- The Qt-text-PDF and matplotlib-PDF paths are removed as well: a single render path is what makes the
  three-renderer numerical-equality test meaningful.

**Silence default:** A.

---

# Secondary decisions — ADOPTED as defaults

(Objection window open until G0. If no objection by then, these are finalised in `docs/assumptions.md`.)

| # | Topic | Adopted | Rationale |
|---|---|---|---|
| **S1** | Python version | Pin **3.11.9** (not 3.12/3.13) | torch/mapie/PyInstaller compatibility and stable Windows `multiprocessing`; upgrade considered in Phase 9 |
| **S2** | Git history | **Forward deletion only**, no rewrite/force-push | Next/React files are removed from HEAD; rewriting history adds risk with no benefit |
| **S3** | Heavy compute | `spawn` process pool with **file-backed payloads** >1 MiB | Phase 1; matches the prompt |
| **S4** | Code quality | `ruff` (lint + format) + `pyflakes` | Fast, cheap, opinion-free |
| **S5** | Coverage | **60 %** for `core/` in Phases 0–6; **75 %** for `core/diagnostics`, `core/statistics`, `core/uq` from Phase 4 | 100 % is unrealistic, but “19 tests for 7,700 LOC” must end |
| **S6** | Sample artifacts | `samples/*.xlsx` generated by script in CI, excluded from git | Prevents tests depending on uncommitted files (one such failure exists today) |
| **S7** | License | ✅ **Proprietary / Internal** (owner-ratified 2026-11-12); `LICENSE` committed | Ownership retained; external distribution requires a separate written agreement |

---

# Invariants (no decision can override these)

1. **Python-only, PySide6, offline, Windows-native** — no Node/Electron/Tauri/WebView/HTTP server (G-1, G-2, AC-01, AC-35).
2. **SQLite-first** with a zero-fail fallback ladder (DB-1..11).
3. **VSG and TCP before any inferential test** (PIPE-1, AC-23, AC-24).
4. **No LLM in the report path** and **no hardcoded numbers** (G-9, G-11, RPT-4).
5. **No unlogged data deletion or mutation** (G-6) and **no pickle without SHA-256** (PER-6).
6. **E3 evidence for Windows IDs** — verbal claims are not accepted.
7. **Tests are never tampered with** to go green (rule 4.1-2 of the plan).

---

# Ratification register

| # | Option chosen | Date | Decider | Recorded effect |
|---|---|---|---|---|
| D1 | **B** — defer + ready signing pipeline | 2026-11-12 | owner | PKG-3 → [S]; trigger before external distribution |
| D2 | **B+A** — Windows CI + physical reference machine | 2026-11-12 | owner | SCR-6 numbers only from reference hardware; E3 mandatory |
| D3 | **A** — Full default + optional Lite | 2026-11-12 | owner | AC-10 proven on Full; Lite is a known limitation |
| D4 | **B** — versioned JSON + nightly R job | 2026-11-12 | owner | evidence expires after 14 days without a run |
| D5 | **A** — ReportLab only | 2026-11-12 | owner | full REP-3; WeasyPrint banned repository-wide |
| S7 | **Proprietary / Internal** | 2026-11-12 | owner | `LICENSE` + `THIRD_PARTY_NOTICES.md` required in every build |

**Signature:** reviewer — decision bundle closed (D1..D5 + S7); Phase 0 open; Phase 1 closed until Phase 0 passes.
