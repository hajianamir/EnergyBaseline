# Capability implementation program

## Honest scope and second-pass search

This program tracks the user's request to make the previously missing capabilities executable. It is **not complete**. Large independent application families (survival, graph ML, NLP, computer vision, recommenders, financial risk, optimization, simulation) are not represented by empty adapters, simulated results, or renamed regression functions.

The second-pass investigation searched all **146 production Python files** at pre-change commit `f4f14ca`, collected **1,449 AST import entries**, searched candidate algorithm identifiers across 22 capability groups, and probed 11 actual installed public backend APIs. Raw results: `artifacts/capabilities/quality-stage1/deep-search.json`. Candidate name matches are navigation aids, not proof of semantic use: e.g. a missing-architecture message containing “TFT” does not implement TFT.

Actual GitHub API lookups also identified 16 external backend repositories for fairness, survival, graph ML, NLP, vision, recommenders, operations research, simulation, explanations, AutoML, forecasting, causal inference, Bayesian inference and validation. Raw source URLs/license metadata are in `artifacts/capabilities/quality-stage1/external-candidates.json`. These are research candidates, **not new executable features**. Discovery does not install backends or extend their product integration; the artifact separately records already-installed distributions (including the narrower existing EconML adapter). Also, `NOASSERTION` license metadata is not license clearance, and Python/Windows/Linux/offline compatibility remains unverified.

The installed SciPy, sklearn, and statsmodels backends already provide MILP/LP, RFE/RFECV/sequential selectors, Lilliefors, several additional tests and multiplicity methods. These APIs were actually imported and inspected, but **are still pending product integration**. Merely importing them in the investigation is not an application feature. No new dependency or hash lock was needed for Stage 1.

`CAPABILITY_BACKLOG.json` preserves every partial/not-found/not-implemented row from the original audit (40 rows, some grouping several independent algorithms). It explicitly distinguishes newly implemented bounded functionality from pending broader subsystems. The original audit remains a historical snapshot, not a current claim that the new quality engine is absent.

## Stage 1: six-axis read-only quality evidence

Implementation: `energybaseline/core/quality_audit.py::audit_quality`. Stage 1 targets scalar tabular business data; numeric rules are defined for real-valued numeric columns, not complex-valued data or arbitrary Python objects.

Execution paths:

- CLI: `python -m energybaseline.cli quality-audit ...`.
- Desktop: Setup -> **Data Quality (Six Dimensions)**. The tab runs completeness/uniqueness on raw data at import. Explicit policy, reference and as-of controls enable the remaining axes.
- Python API: `audit_quality(frame, policy, references={name: dataframe}, as_of=...)`.

| Axis | Actual calculation |
|---|---|
| Completeness | Nonmissing selected cells / selected cells; default selects all columns. |
| Uniqueness | Rows with nonmissing declared keys and no preceding duplicate key / rows; default key is the entire selected row. |
| Validity | Passed row-rule evaluations / all row-rule evaluations. Supported declarative rules: `not_null`, numeric `finite`, closed numeric `range`, and `allowed` values. Missing values fail applicable validity rules. |
| Consistency | Passed row-rule evaluations / all row-rule evaluations. Cross-column comparisons `eq/ne/lt/le/gt/ge`, and composite foreign-key membership in an explicitly supplied reference table. Missing keys fail membership; missing compared values fail comparison. |
| Accuracy | Agreement with nonmissing reference truth (also finite for numeric truth) on declared columns, after one-to-one key matching, with optional nonnegative numeric `atol`/`rtol`. Unknown/missing reference truth is unassessed, not correct. Reference coverage is reported independently. Missing observations with known truth fail. |
| Timeliness | Rows whose parsed timestamp age lies in the explicit SLA interval `[-future_tolerance_seconds, max_age_seconds]` relative to an explicit timezone-aware as-of time. Invalid, stale and excessively future timestamps fail. This is a row-age SLA, not merely the newest record's freshness. |

These are documented operational definitions, not universal definitions of data quality. Reference agreement does not certify the reliability of the supplied reference itself. This engine does not replace Pandera/Great Expectations or claim a general business-rule DSL.

### Missing evidence is not a perfect score

Unconfigured axes have `status="not_assessed"` and `score=null`. Accuracy with incomplete truth has `status="partial"` and explicit coverage. Empty data does not produce a perfect score. `overall_score` is available **only when all six axes are fully assessed**; otherwise it is null. `assessed_score` is an explicitly incomplete equal-axis average and must not be presented as a complete six-axis score. Scores use [0,1]; the GUI displays the complete score as a percentage when available.

Within validity/consistency, every row-rule pair has equal weight. Across dimensions, each axis has equal weight. Failed checks include up to 20 **zero-based input row positions**, never cell values. Completeness instead reports row-major flattened selected-cell offsets and marks the position kind explicitly.

### Matching and trust boundaries

Accuracy rejects duplicate or missing matching keys in either input. Numeric-reference accuracy requires a numeric observed dtype; mixed/string columns must be explicitly parsed by the caller instead of silently coerced and rounded. Zero-tolerance comparisons and reference alignment preserve exact integers above 2**53 as represented in the supplied DataFrames (precision already lost during earlier parsing cannot be recovered); nonzero-tolerance accuracy on such integers requires explicit rescaling rather than silent float-rounding matches. Numeric range rules preserve integer limits as well. Reference row order does not matter. Key type conversion is never guessed: timestamp keys can be explicitly normalized with `"key_types": {"timestamp": "datetime_utc"}`. The normalization is applied to copies and detects timezone-equivalent duplicate keys. Unknown reference rows/values remain unassessed. Reference tables are provided by the caller; a policy cannot open a path or execute Python, SQL, a string expression, or an arbitrary function.

Timeliness requires a caller-supplied as-of; it does not secretly read the wall clock. Source timestamp strings without an offset are interpreted as UTC by this explicit contract. Local wall-clock data should be converted to the correct timezone before auditing if UTC semantics are inappropriate.

Every result binds the raw input, policy and used reference tables by SHA-256 and records normalized as-of time. Serialized results contain counts, positions, labels and digests, not raw/ground-truth values or policy literals. The audit neither edits input/reference data, imputes targets, trains/refits models, accepts VSG, nor changes/locks TCP. It is descriptive, so it can run before inference authorization.

The GUI clears policy/references on dataset reload. Quality references remain in memory and are **not persisted into `.ebproj`**. Policies and explicit reference datasets must be reloaded when needed. A malformed policy or disallowed path clears displayed evidence instead of retaining a stale result. Adding a reference may leave the audit blocked until the policy's other requirements (e.g. as-of) are supplied; press Run Read-Only Audit once configured.

### Reproducible example

The tiny synthetic inputs under `examples/quality` are executable documentation, not user data or a benchmark:

```bash
python -m energybaseline.cli quality-audit examples/quality/input.csv \
  --policy examples/quality/policy.json \
  --reference truth=examples/quality/reference.csv \
  --as-of 2026-01-01T04:00:00Z \
  --fail-under 1 \
  --out quality-result.json
```

Exit codes: 0 = audit executed (and any requested complete-score threshold passed); 1 = requested overall-score threshold failed **or evidence was incomplete**; 2 = invalid input/configuration. Without `--fail-under`, failed data checks are reported as audit findings rather than treated as command/configuration failure.

GUI steps: load the input CSV, open the new quality tab, add `reference.csv` with name `truth`, enter the explicit as-of above, load `policy.json`, and run. The API and CLI accept multiple named references; only explicit user bindings are loaded.

### Deliberately unchanged integration contracts

The legacy four-component preprocessing score in `core/quality.py::quality_score` remains unchanged. The six-axis engine is a separate raw-data audit in Setup/CLI, **not** an unreviewed replacement for model scoring or an addition silently inserted into every analysis ReportModel/PDF. Report renderer contracts and baseline acceptance policy are unchanged. No new inferential procedure runs before accepted VSG/locked TCP.

### Verification status

Focused unit/CLI and real Qt integration tests are under `tests/test_quality_audit.py` and `tests/gui/test_quality_audit_gui.py`. They cover reference matching, absence of evidence, null/duplicate keys, malformed policies, row-level SLAs, failure positions, explicit timezone normalization, privacy, input immutability, structured CLI errors/thresholds, GUI reachability, dataset reload, path guards, and preservation of accepted gate objects and engine call counts.

Stage 1 verification on source `ef78c2b6bd0e2a8b81ef7220007950337b3d73cf`: **48 focused tests, 25 complete GUI-directory tests, and 552 full-suite local tests passed**, all with exit 0 and no skips. Fresh [Windows/Linux workflow 43724721792](https://github.com/hajianamir/EnergyBaseline/actions/runs/43724721792) completed successfully on both OSes, including full-suite/coverage and GUI/coverage steps. Raw logs, JUnit, actual hosted API receipts and source binding are in `artifacts/capabilities/quality-stage1/README.md`. Earlier-source runs are not substituted for this final implementation. The follow-up documentation/evidence commit does not change tested product, tests, dependencies or workflows. This verifies Stage 1, **not completion of the broader program**.

## Stage 2 executable expansion

See [EXPLORATORY_WORKBENCH.md](EXPLORATORY_WORKBENCH.md) for 19 bounded offline operations, four additional integrated TCP corrections, native process-pool desktop execution, CLI/API contracts, 25 executable synthetic examples and all limits. This is a substantial but explicitly partial expansion of 22 backlog rows. Stage 2 is verified on source `5e571cc` with 120 focused / 28 GUI / 672 full-suite local passes, 88.51% expanded coverage and successful Windows/Linux workflow `43737625559`. Actual receipts and limitations are recorded separately from Stage 1 in `artifacts/capabilities/workbench-stage2/README.md`; the whole program remains incomplete.

## Remaining broader implementation stages (partially addressed, not complete)

1. **Statistical extensions and feature pipeline:** additional tests/corrections with preregistered hypotheses and dependence assumptions; prospective power and Bayesian posterior/MCMC/VI contracts; in-fold selectors, transforms, categorical imputation; additional baseline candidates. Do not expose unrestricted shuffled CV for temporal datasets or retrospective observed-power claims.
2. **Monitoring, risk, fairness and robustness:** categorical/divergence/sequential drift contracts; explicitly defined loss/return risk semantics; sensitive-attribute/outcome schemas and appropriate fairness metrics; reproducible stress scenarios that do not mutate a locked baseline. Existing optional APIs require actual application integration, not installation badges.
3. **Explanations, causal and forecasting:** bounded ALE/LIME/recourse contracts and immutable model evaluation; identification assumptions for IV/RD/synthetic control/CATE; missing forecasting architectures with temporal benchmarks and optional native backends. Do not relabel feature importance as causal explanation.
4. **Other application families:** survival censoring/event schema; graph node/edge schema; text/image inputs and offline model provenance; recommender user/item/event schema. Each needs its own meaningful evaluation and user interface rather than coercing every input into the temporal regression target editor.
5. **Operations research, simulation and larger AutoML engines:** explicit decision variables, constraints, objective/feasibility checks, deterministic simulation scenarios and backend/license/resource contracts. Availability of `scipy.optimize.milp` or Optuna is not integration of every scheduler/simulator/AutoML system.

All stages remain subject to unchanged Python 3.12/hash-lock, privacy, web-free/native UI, VSG/TCP, input binding, no-target-imputation, in-fold leakage, immutable baseline, ReportModel-only renderer and real Windows/Linux evidence requirements. This roadmap does not assert that the remaining capabilities are already executable.


## Stage 3 preregistered statistics and business policies

See [PREREGISTERED_STATISTICS.md](PREREGISTERED_STATISTICS.md) for 27 gated statistical method identifiers, exact-plan TCP registration, native spawn-worker/CLI execution and composable business-validation assertions. Bounded additions address C003/C005 and C008–C015 without claiming every possible design/backend or completing other pending rows. Final-source execution and actual Windows/Linux status are recorded in `artifacts/capabilities/statistics-stage3/README.md`; earlier-stage CI is not substituted for Stage 3.

Stage 3 is verified on source `9dc70d5`: **202 focused**, **32 complete GUI** and **754 complete local regression** tests passed with actual exit 0 and no skips. Capability-package coverage is **89.51%** and GUI coverage **72.09%**. The local full regression was uninstrumented; the expanded full-suite 60/80% coverage gates passed in actual final-source [Windows/Linux workflow 43749525830](https://github.com/hajianamir/EnergyBaseline/actions/runs/43749525830). Raw local logs, source binding and completed API receipts are preserved in the Stage 3 evidence directory. This is not whole-program completion.


## Stage 4 advanced workbench expansion

See [ADVANCED_WORKBENCH.md](ADVANCED_WORKBENCH.md) for twelve additional executable operations covering actual AutoML/NAS, six scratch-trained forecasting reference variants, 2-D vision, CVRP/job-shop/NSGA-II and stock-flow/agent/twin simulation. Native spawn-worker, API and CLI paths share explicit JSON contracts with nineteen executable jobs. Actual verification status is tracked separately in `artifacts/capabilities/advanced-stage4/README.md`. This does not silently integrate named external engines, pretrained models or the accepted main candidate lifecycle; full-program completion remains false.

Stage 4 is verified on source `8475dd2`: **192 focused**, **41 complete GUI** and **825 complete local regression** tests passed with actual exit 0 and no skips. Workbench coverage is **88.57%** and GUI coverage **72.10%**. All **19** standalone CLI jobs completed successfully. Actual final-source [Windows/Linux workflow 43757468182](https://github.com/hajianamir/EnergyBaseline/actions/runs/43757468182) completed successfully, including unchanged full-suite 60/80% and native GUI coverage gates. Completed hosted API receipts, raw local execution evidence and zero-drift source binding are retained in the Stage 4 evidence directory. Named external engines, pretrained models, complete paper parity, other pending catalog families and per-row remaining scope are not declared complete.

## Stage 5 actual external engines and pretrained OCR

See [EXTERNAL_ENGINES.md](EXTERNAL_ENGINES.md) and [THIRD_PARTY_MODELS.md](THIRD_PARTY_MODELS.md) for actual FLAML/Pandera integration and offline pretrained PP-OCR detection/orientation/recognition. Three new operations bring the registry to 35. Executions are not package-availability badges; recognition is checked against a real semantic oracle, and independent/native/frozen engine runs are required. Final-source verification status is recorded in `artifacts/capabilities/external-stage5/README.md`. Runtime conflicts, unavailable weight hosts, untouched external engines and all remaining catalog exclusions remain open. This is not full-program completion.

Stage 5 is verified on source `cc6be99`: **244 focused**, **44 complete GUI** and **877 complete local regression** tests passed with actual interpreter-teardown exit 0 and no skips. Workbench coverage is **88.95%** and GUI coverage **72.10%**. All three standalone CLI operations succeeded, as did real execution with connections/DNS denied. Actual [Windows/Linux workflow 43779750271](https://github.com/hajianamir/EnergyBaseline/actions/runs/43779750271) passed the unchanged full/GUI coverage gates and all three operations through the **windowed Windows executable**. Separate [console diagnostic workflow 43779750245](https://github.com/hajianamir/EnergyBaseline/actions/runs/43779750245) also passed on the same source. Historical failures exposed absent windowed streams, a native-logging test assumption and a missing bundled XGBoost DLL; their receipts and genuine fixes remain recorded in the evidence directory. No backend was disabled, mandatory check skipped, timeout increased or baseline/report gate weakened. This verifies the bounded Stage 5 integrations, not every external engine, paper architecture or remaining catalog family.


## Stage 6 NLP, predictive survival and graph learning

Four additional executable operations implement train-only TF-IDF text classification/NMF topics, Cox/Breslow and Weibull AFT likelihood optimization with right censoring/delayed entry, and a real static transductive two-layer CPU GCN. Five synthetic CLI/native-worker jobs exercise actual fitting and semantic/numeric oracles. See [CATALOG_EXPANSION.md](CATALOG_EXPANSION.md) for contracts, strict limits, privacy and explicit transductive semantics. The shared binary helper now rejects silent boolean/complex/object coercion; real integer/float zero/one inputs remain supported. Mandatory baseline tests and inference authorization are unchanged. Verification status is recorded in `artifacts/capabilities/catalog-stage6/README.md`; no Stage 5 result is substituted for current-source acceptance. All other requested work and per-row remaining scope remain open.


Stage 6 is verified: **322 focused**, **49 complete GUI** and **975 complete local regression** tests passed with actual interpreter-teardown exit 0 and no skips on `76159f4`. Workbench coverage is **89.25%**, GUI coverage **72.10%**. Actual [Windows/Linux workflow 43834852648](https://github.com/hajianamir/EnergyBaseline/actions/runs/43834852648) succeeded on `742ab33`, including unchanged expanded full/GUI coverage gates, existing lite installer/runtime/external-engine checks and all five new semantic jobs through the **full windowed Windows executable**. Separate [full-console workflow 43834852664](https://github.com/hajianamir/EnergyBaseline/actions/runs/43834852664) also passed. All 436 product/test/fixture/dependency/script blobs are identical between local and hosted sources; only the two explicitly recorded CI workflows changed. Local runs are not falsely represented as reruns of that workflow-only commit. Five additional actual CLI processes with absent Python stdout/stderr also passed on `742ab33`. Raw local logs/JUnit, completed hosted API receipts, source equivalence, actual topology/likelihood/gradient oracles and prior failure reproductions are retained in the Stage 6 evidence directory. This is another verified bounded increment, not whole-catalog or full-paper completion.
