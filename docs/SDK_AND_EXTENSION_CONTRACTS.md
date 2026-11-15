# Analysis SDK, domain and extension contracts

## Public analysis API v1

`energybaseline.contracts.AnalysisRequest` is the versioned input boundary.
Timestamp and target roles are explicit. Unsupported versions, unknown request
fields, ambiguous target lists and inconsistent domain declarations are rejected.
The constructor snapshots nested caller configuration. Frozen dataclasses do not
make nested Python objects intrinsically immutable; do not mutate request config
while executing a request.

```python
from energybaseline.contracts import AnalysisRequest
from energybaseline.sdk import analyze_request

request = AnalysisRequest(
    time_col="timestamp",
    targets=("quality_offset",),
    domain_pack="manufacturing",
    config={"seed": 42, "hpo_trials": 0, "model_families": ["linear"]},
)
response = analyze_request(frame, request)
summary = response.to_summary()
# summary is JSON-native; it does not contain executable estimators.
# response.run and response.bundles are trusted in-process Python objects.
```

`AnalysisRequest.from_dict` accepts the same fields plus `schema_version: 1`.
`AnalysisResponse.to_summary()` exports `energybaseline.analysis.v1`, version 1,
domain policy, cancellation state, and per-target status, shortlist, scoring,
validation contract, purge specification and acceptance limits. Missing numeric
evidence is represented as JSON null rather than non-standard NaN/Infinity.

The tuple-returning `sdk.analyze` remains supported and validates through the same
request boundary. The historical `core.pipeline.run_analysis` still records an
explicit deprecated `legacy_energy` policy when old callers omit a domain. It is
not the neutral public default. The separate `InFoldModelPipeline`, F1..F10 APIs,
and deprecated `scoring-register` are compatibility/research utilities, not
alternative authoritative production ranking endpoints. Their historical
energy-oriented metric contracts have not been silently relabeled as neutral.

## Built-in domain policy contract v1

`core.domain.PUBLIC_DOMAIN_PACKS` and the immutable built-in registry define:

| Pack | Energy-specific features and limits | Intended scope |
| --- | --- | --- |
| `neutral` | Disabled | Generic temporal regression |
| `energy` | Explicit opt-in | Degree-day features and recognized calibration limits |
| `water` | Disabled | Volume/flow regression with declared units and roles |
| `manufacturing` | Disabled | Signed quality/process regression with declared units and roles |

Water and manufacturing currently reuse neutral statistical semantics. Their
names do not imply regulatory compliance, target positivity, universal limits,
or physical laws inferred from column names. Process-specific acceptance limits
must be supplied explicitly. Each resolved pack records key, policy version and
energy capability in results and executable bundles.

Neutral/water/manufacturing schema suggestions do not derive energy units,
flow-aggregation semantics or known-in-advance availability from energy-like
column names. VSG does not apply energy-name relevance bonuses or the energy-only
submeter-name correlation heuristic in those packs. Exact/affine target leakage,
sibling-target exclusion and temporal integrity remain mandatory in every pack.
Explicit ColumnSpec declarations override suggestions.

The immutable unit registry is a common physical service, not an energy-domain
switch. It supports energy, power, temperature, volume, flow, ratio, pressure,
mass and duration conversions. Cross-dimension conversions fail. Unknown declared
units are preserved with an explicit normalization message, not silently assigned
a fabricated conversion factor. SDK callers provide analysis-ready values;
desktop unit normalization occurs before gate acceptance and uses the same
normalized frame for the analysis request. Call `ingest.normalize_units`
explicitly before creating SDK gate objects when conversion is needed.

## Executable extension trust boundary

There is no directory scan, arbitrary entry-point import, dynamic `eval`, or
third-party executable plugin loader. Unknown pack identifiers are rejected.
Adding a built-in domain policy or unit definition is a reviewed source change;
installing a Python package does not grant it execution through the application.
This is the deliberately bounded v1 extension contract, not a plugin marketplace
or a sandbox. Runtime third-party hooks remain unsupported until a separate
provenance, permission and worker-isolation contract is accepted.

Serialized Python estimators are executable extensions too. `store.load_run` and
`store.load_bundle` accept optional `trusted_artifact_sha256`; locked-record loading
uses `trusted_record_sha256`. Artifacts issued by application code in the current
process are approved implicitly. External and previous-session artifacts require
an independently approved exact digest. A nearby sidecar or manifest is integrity
evidence, never an independent approval source. The desktop asks for a digest and
an explicit executable-code trust confirmation; approval is not prefilled from
the untrusted sidecar. Declining remains fail-closed.

For example, after independently verifying a producer's artifact digest:

```python
from energybaseline.core import store

run = store.load_run(run_id, trusted_artifact_sha256=approved_run_digest)
bundle = store.load_bundle(run_id, target, model_name, trusted_artifact_sha256=approved_bundle_digest)
```

Checks hash the exact buffer that is subsequently deserialized. This does not
make joblib safe for untrusted input or provide cryptographic author signatures.
The process-issued registry does not survive restart. Old files missing integrity
sidecars must be recreated or explicitly migrated in a trusted environment;
approving a digest does not bypass a missing/corrupt integrity sidecar.

## Report and test-result boundaries

Renderers continue to consume only immutable `ReportModel` schema v1; adapter
code alone inspects analysis dataclasses. Domains add facts/tables/charts through
that boundary, not by handing executable models to a renderer. New report
extensions must preserve artifact-bound narrative and numeric agreement checks;
PDF extensions must remain ReportLab Platypus based.

Skipped catalog results now use the same `TestContractResult` contract as
executed results, with `schema_version: 1` on dictionary export. No-authorization,
user-disabled and no-group results carry null statistics, not dummy p-values.
Application-level auxiliary EDA and residual p-values are disabled; canonical
inferential results come from the TCP-controlled catalog. Low-level statistical
primitives remain explicit library/research APIs, not an authorization shortcut
for application orchestration.

The desktop/CLI/SDK analysis routes require accepted input-bound VSG and locked
TCP before inference. Historical direct cached runners retain their accepted-only
call shape with a deprecation warning: they verify the unchanged accepted
specification, lock it before any primitive, and then enforce the same input and
preregistration binding. Amendments after that point require an explicit unlock,
reason, reacceptance and lock. Unaccepted or directly mutated panels fail before
engine calls.

## Lock migration

`BaselineLockManager.lock_baseline` now defaults to neutral policy and no universal
acceptance thresholds. Select `domain_pack="energy"` explicitly for energy model
card context. Built-in energy limits require a recognized declared calibration
interval; daily/weekly do not inherit invented standard limits. Explicit supplied
thresholds are retained as supplied policy, not certified standards.

The historical serialized field name `iso50006_model_card` remains readable for
compatibility. Neutral card contents have no governing energy standards or EnPI
claim. Energy card context is not a certificate of compliance.

`store.lock_baseline` verifies the bundle and persists the immutable lock before
changing the active selection. Missing, unapproved or unwritable artifacts fail
closed without replacing the active selection. A subsequent DB failure may leave
an inactive immutable audit record, not a successful active selection.
