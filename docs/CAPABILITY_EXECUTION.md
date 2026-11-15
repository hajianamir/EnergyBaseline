# Capability implementation program

## Honest scope and second-pass search

This program tracks the user's request to make the previously missing capabilities executable. It is **not complete**. Large independent application families (survival, graph ML, NLP, computer vision, recommenders, financial risk, optimization, simulation) are not represented by empty adapters, simulated results, or renamed regression functions.

The second-pass investigation searched all **146 production Python files** at pre-change commit `f4f14ca`, collected **1,449 AST import entries**, searched candidate algorithm identifiers across 22 capability groups, and probed 11 actual installed public backend APIs. Raw results: `artifacts/capabilities/quality-stage1/deep-search.json`. Candidate name matches are navigation aids, not proof of semantic use: e.g. a missing-architecture message containing “TFT” does not implement TFT.

The installed SciPy, sklearn, and statsmodels backends already provide MILP/LP, RFE/RFECV/sequential selectors, Lilliefors, several additional tests and multiplicity methods. These APIs were actually imported and inspected, but **are still pending product integration**. Merely importing them in the investigation is not an application feature. No new dependency or hash lock was needed for Stage 1.

`CAPABILITY_BACKLOG.json` preserves every partial/not-found/not-implemented row from the original audit (40 rows, some grouping several independent algorithms). It explicitly distinguishes newly implemented bounded functionality from pending broader subsystems. The original audit remains a historical snapshot, not a current claim that the new quality engine is absent.

## Stage 1: six-axis read-only quality evidence

Implementation: `energybaseline/core/quality_audit.py::audit_quality`.

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
| Accuracy | Agreement with nonmissing reference truth on declared columns, after one-to-one key matching, with optional nonnegative numeric `atol`/`rtol`. Unknown/missing reference truth is unassessed, not correct. Reference coverage is reported independently. Missing observations with known truth fail. |
| Timeliness | Rows whose parsed timestamp age lies in the explicit SLA interval `[-future_tolerance_seconds, max_age_seconds]` relative to an explicit timezone-aware as-of time. Invalid, stale and excessively future timestamps fail. This is a row-age SLA, not merely the newest record's freshness. |

These are documented operational definitions, not universal definitions of data quality. Reference agreement does not certify the reliability of the supplied reference itself. This engine does not replace Pandera/Great Expectations or claim a general business-rule DSL.

### Missing evidence is not a perfect score

Unconfigured axes have `status="not_assessed"` and `score=null`. Accuracy with incomplete truth has `status="partial"` and explicit coverage. Empty data does not produce a perfect score. `overall_score` is available **only when all six axes are fully assessed**; otherwise it is null. `assessed_score` is an explicitly incomplete equal-axis average and must not be presented as a complete six-axis score. Scores use [0,1]; the GUI displays the complete score as a percentage when available.

Within validity/consistency, every row-rule pair has equal weight. Across dimensions, each axis has equal weight. Failed checks include up to 20 **zero-based input row positions**, never cell values. Completeness instead reports row-major flattened selected-cell offsets and marks the position kind explicitly.

### Matching and trust boundaries

Accuracy rejects duplicate or missing matching keys in either input. Reference row order does not matter. Key type conversion is never guessed: timestamp keys can be explicitly normalized with `"key_types": {"timestamp": "datetime_utc"}`. The normalization is applied to copies and detects timezone-equivalent duplicate keys. Unknown reference rows/values remain unassessed. Reference tables are provided by the caller; a policy cannot open a path or execute Python, SQL, a string expression, or an arbitrary function.

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

Final local and fresh two-OS results are recorded in `artifacts/capabilities/quality-stage1/README.md` after execution. Stage 1 must not be called verified on the strength of the previous VSG-fix workflow.

## Remaining implementation stages (not yet implemented by this program)

1. **Statistical extensions and feature pipeline:** additional tests/corrections with preregistered hypotheses and dependence assumptions; prospective power; in-fold selectors, transforms, categorical imputation; additional baseline candidates. Do not expose unrestricted shuffled CV for temporal datasets or retrospective observed-power claims.
2. **Monitoring, risk, fairness and robustness:** categorical/divergence/sequential drift contracts; explicitly defined loss/return risk semantics; sensitive-attribute/outcome schemas and appropriate fairness metrics; reproducible stress scenarios that do not mutate a locked baseline. Existing optional APIs require actual application integration, not installation badges.
3. **Explanations, causal and forecasting:** bounded ALE/LIME/recourse contracts and immutable model evaluation; identification assumptions for IV/RD/synthetic control/CATE; missing forecasting architectures with temporal benchmarks and optional native backends. Do not relabel feature importance as causal explanation.
4. **Other application families:** survival censoring/event schema; graph node/edge schema; text/image inputs and offline model provenance; recommender user/item/event schema. Each needs its own meaningful evaluation and user interface rather than coercing every input into the temporal regression target editor.
5. **Operations research, simulation and larger AutoML engines:** explicit decision variables, constraints, objective/feasibility checks, deterministic simulation scenarios and backend/license/resource contracts. Availability of `scipy.optimize.milp` or Optuna is not integration of every scheduler/simulator/AutoML system.

All stages remain subject to unchanged Python 3.12/hash-lock, privacy, web-free/native UI, VSG/TCP, input binding, no-target-imputation, in-fold leakage, immutable baseline, ReportModel-only renderer and real Windows/Linux evidence requirements. This roadmap does not assert that the remaining capabilities are already executable.
