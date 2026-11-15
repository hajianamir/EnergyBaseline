# Architecture, Security and Generalization Review

## 1. Scope, source and decision boundaries

**Reviewed commit:** `fe345ded40aae652fe1fb99617ca314211d9e5cf`  
**Review branch:** `arena/5b0e8eef-energybaseline`  
**Request:** assess the supplied critique and roadmap, not implement a product rewrite.

This review covers the credential/SQL/storage issues, duplicate analytical paths,
statistical validity, domain-pack proposal, dependency/platform policy, language
requirements, SDK/server scope and competitive assertions in the supplied text.

**Binding language decision: the product remains English-only.** No Persian UI,
Persian report templates, RTL layout, translation catalog or language selector is
recommended as part of the present work. This also agrees with G-7 and the existing
English-only decision in `docs/DECISIONS.md:9`. This report and its reproducible
companions are English. Discussing the findings with the owner in Persian does
not change the product language.

Only review artifacts were created. Application code, dependency manifests,
workflows, frozen tests, baseline artifacts and requirement statuses were not
modified. No commit, push, PR, issue closure or merge was performed for this review.

### Evidence levels and limits

- **E — executed isolated reproduction:** unchanged selected source definitions or
  expressions were executed with synthetic inputs; no database was contacted.
- **S — source-confirmed:** the current source establishes the behavior/data flow.
- **I — inference/design assessment:** a plausible consequence or recommendation,
  not an executed end-to-end experiment.
- **X — not independently established:** external/comparative or quantitative
  claims for which this review has no adequate evidence.

`review_probe.py`, `probe_results.json`, `inventory.json`, `probe_run.txt` and
`static_checks.txt` accompany this report. The probes execute selected definitions
from the reviewed source, rather than importing the entire application. This is
explicitly **not** a production integration test, penetration test, scientific
benchmark, full pytest run, new pip-audit result, live R comparison or physical GUI
acceptance. No new full-suite/coverage/CI success is claimed. The review environment
uses Python 3.11.2 and the project's pinned `sqlglot==30.31.0` for the SQL probes.

P0 below means a release/expansion blocker for the affected use case, not a claim
that a remotely exploitable critical vulnerability has been demonstrated. Risk
ratings are qualitative; no CVSS score is asserted without a concrete deployment
and attacker model.

## 2. Executive judgment

**The central architectural diagnosis is substantially correct:** a reusable
analytics engine exists, but the active desktop path still mixes energy-specific
policy with data preparation, fitting, scoring and presentation. Calling it a
validated general-purpose workbench today would overstate its readiness.

**The correct response is a controlled migration, not wholesale replacement.**
The existing provenance, gated tests, immutable reporting boundary, baseline lock,
read-only SQL parser and fold-local preprocessing components are assets to retain.
Several proposed drop-in replacements would introduce regressions or violate
current contracts.

The most important findings are:

1. **Credential exposure is confirmed and broader than the critique states.**
   `_mask` misses query and ODBC secrets. Independently, the database self-test logs
   a dictionary containing the raw external URL, bypassing even correct userinfo
   masking. Driver exception text is another unsanitized channel.
2. **The active desktop preprocessing path still learns from the full evaluation
   dataset before folds are made.** Fold-local median handling downstream cannot
   undo globally fitted balance points, selectors or already-filled values. This
   is more consequential than the name of the correlation leakage heuristic.
3. **Two scoring systems are active concepts, not just duplicate constants.**
   They have different criterion schemas. The displayed ranking is based on the
   legacy policy; the later sensitivity calculation uses the newer policy's
   defaults. Simply importing the new weight dictionary is incorrect.
4. **The temperature-name bug and query-value interpolation are confirmed.**
   The supplied regex fix misses common underscore-separated names, and the SQL
   fix must change execution, ODBC adaptation, persistence and GUI state together.
5. **Domain packs are a sound destination, but only after one authoritative
   leakage-safe execution path and a versioned scoring/acceptance contract exist.**
6. **English-only is a product requirement, not a defect.** The proposed Persian
   localization program is out of scope. Unicode user-data rendering is a separate,
   narrower issue.

## 3. Measured inventory and corrections to numerical claims

The following counts are reproducible from `inventory.json`; physical line counts
include comments, docstrings and blank lines and do not measure complexity alone.

| Observation | Current source |
|---|---|
| Python modules under `energybaseline/` | 139 |
| Exact `except Exception` handlers | 89 |
| Bare `except:` handlers | 0 |
| `except Exception` handlers whose entire body is `pass` | 18 |
| `TargetResult` annotated fields / direct `dict` fields | 37 / 14 |
| `RunResult` annotated fields / direct `dict` fields | 13 / 7 |
| `core/models.py` / `core/pipeline.py` | 731 / 741 physical lines |
| `run_analysis()` | 85 physical lines, lines 657–741; **not** a 700-line function |
| `analyze_target()` | 420 physical lines, lines 201–620 |
| Largest inspected function | `quality.preprocess_target`, 424 physical lines |
| `gui/pages_setup.py` | 1,552 physical lines, not 1,608 at this commit |
| `core/report.py` | 117 physical lines; already a compatibility boundary |
| ASHRAE/IPMVP tokens in product Python source | 103 occurrences in 16 files |
| Unit-registry aliases | 39; several map to the same canonical unit |
| Direct requirements / resolved text entries / lock entries | 47 / 169 / 169 |

The domain-token count uses case-insensitive `ASHRAE|IPMVP` across
`energybaseline/**/*.py`, including identifiers/comments/docstrings. A different
scope could produce the submitted 52/22 count. The coupling conclusion should be
based on dependency direction and policy ownership, not that unqualified count.

All named/versioned requirements in `requirements.in` and `requirements.txt` are
present with matching versions in `requirements.lock` under a simple name/version
comparison. This does **not** validate marker equivalence, every platform's wheel
availability or dependency resolution; it does show that current version drift
between these manifests was not established by the critique.

## 4. Disposition of every principal critique

| Submitted item | Disposition | Priority / qualification |
|---|---|---|
| A-1 `_mask` leaks credentials | **Confirmed, E/S; understated** | P0: include raw self-test/status/error channels, not only masking |
| A-2 temperature regex matches unrelated names | **Confirmed, E** | P0 correctness fix; supplied regex is not a safe complete replacement |
| A-3 SQL values interpolated | **Confirmed, E/S** | P0 hardening; no working remote MySQL exploit is claimed |
| A-4 external DB silently falls back | **Behavior confirmed, S; wording qualified** | It is logged/audited/bannered, not literally silent. Strict policy is needed for required external-storage deployments |
| A-5 key file beside encrypted data | **Confirmed, S; status issue worse than stated** | P0 for non-Windows credential persistence; a 0600 attempt exists, but consent/capability reporting is deficient |
| B-1 duplicate hierarchy | **Substantially confirmed, S; uneven** | Models/scoring/pipeline need migration; reporting is already mostly a shim; mining already delegates some work |
| B-2 energy coupling | **Confirmed, S** | P1 architectural issue for generalization, not automatically a defect in the currently declared energy product |
| B-3 God objects and dict contracts | **Confirmed with corrected sizes, S** | P1 incremental stage/result contracts; no blanket Pydantic conversion |
| B-4 89 broad catches | **Count confirmed, E/S** | Classify recovery paths; 18 pass-only cases deserve individual review; a broad catch with rollback/re-raise is not swallowing an error |
| C-1 leakage screening is insufficient | **Direction correct, details incomplete, S** | P0 scientific validity: full-data preprocessing is the more direct problem; existing screening is more than one fixed Pearson threshold |
| C-2 nested HPO lacks budgets | **Partly correct, S** | Legacy HPO has trial caps but no timeout; the newer nested-HPO helper already has a timeout. No end-to-end target deadline/resume/pruning contract found |
| C-3 only three fixed scoring profiles | **True for desktop settings, incomplete for library, S** | Composite functions already accept weight mappings; unify schemas and wire validated custom policy through GUI/config/provenance |
| C-4 MAPE/denominator handling is healthy | **Substantially supported, S** | Preserve finite/near-zero conventions; applicability to signed/zero-mean generic targets still needs policy |
| D-1 all tests structurally frozen | **Overstated** | Four baseline test files are byte-hashed. Some additional tests are structural; others are behavioral/property-oriented. Retain integrity/security gates |
| D-2 three manifests require immediate replacement | **Maintenance concern, not demonstrated failure** | Preserve hash-locked installations. Improve generation/consistency first; uv is optional tooling, not inherently the fix |
| D-3 Python 3.11-only restriction | **Confirmed, S** | Lifecycle risk; no evidence that current torch/pandas pins necessarily prohibit 3.12/3.13. Add supported-version verification before widening metadata |
| D-4 Windows focus / missing macOS | **Confirmed, explicit scope** | New support commitment, not an accidental regression. macOS must be qualified with real GUI/packaging/security tests |
| D-5 root `pip-audit.py` | **Placement concern, not a security bypass** | It delegates to the real module. Prefer canonical `python -m pip_audit`; merely moving it breaks callers of the current alias |
| E-1 no i18n / limited PDF fonts | **Source-confirmed; proposed language change rejected** | Keep the app English. Consider fonts for supported Unicode data, not a Persian/RTL product project |
| E-2 no multi-user / dual approval | **Confirmed scope gap** | Separate enterprise/authentication/approval architecture; local audit hashes are not two authenticated signatures |
| Domain-pack roadmap | **Recommended with substantial contract additions** | P1/P2, not a drop-in plugin loader |
| Competitor superiority / 70% reuse / fixed schedule | **Not established, X** | Requires versioned comparative research, real migration measurements and staffing assumptions |

## 5. Security and data-integrity findings

### SEC-01 — Credential redaction has multiple independent bypasses

**Evidence:** `config/settings.py:119–126,164–171`,
`db/session.py:179–215,399–424`, `gui/page_status.py:171–195`.

Executed unchanged `_mask` on synthetic values:

| Representation | Does the synthetic secret survive current `_mask`? |
|---|---|
| `postgresql://user:secret@host/db` | No |
| `postgresql://host/db?password=secret` | **Yes** |
| `DRIVER={...};UID=user;PWD=secret;...` | **Yes** |
| URL query with encoded `odbc_connect=...PWD%3Dsecret` | **Yes** |

More importantly, external success stores `url=raw_url` in `_state`.
`self_test()` copies `_state['url']` into `result` and then logs
`json.dumps(result)` at INFO. Thus even a conventional user/password URL can be
logged in plaintext after a successful external connection. The Status snapshot
also returns raw `url` and the entire self-test result. Actual log exposure in
that branch is source-confirmed; no live external database was used in this
review.

`_probe_external_engine` retains `str(exc)` and `get_engine` logs/audits it without
redaction. `sqlsource._wrap` also forwards driver exception text. Whether a driver
includes credentials in a particular failure is driver/input-dependent, but the
unsanitized sink is present.

A precision correction: `db.url_resolved` is a logging event, not itself an ORM
audit insertion. The external-failure path queues an audit record that later
flushes into `AuditLog`. Distinguishing these paths matters when testing coverage.

**Required remediation:**

1. Separate raw connection configuration from a safe diagnostic projection.
   Never serialize a raw connection URL into self-test/status/log/audit payloads.
2. Parse URL authority and query separately; redact known sensitive values,
   repeated parameters, whitespace variants and embedded/encoded ODBC strings.
   Treat malformed/unknown representations conservatively rather than logging
   the original as a fallback. Consider suppressing the entire `odbc_connect` value.
3. Sanitize exception text or record a safe error code/type and an actionable,
   credential-free message. A robust `_mask` alone cannot solve these other sinks.
4. Audit all credential sinks, including UI error dialogs and diagnostic exports.
5. Add canary assertions at logging handlers, audit persistence and status exports,
   for both successful and failing connection paths. Test URL-encoded secrets,
   braces/semicolons, multiple `@` characters and malformed strings.

The submitted two-regex replacement improves common cases but is **not complete**:
encoded `odbc_connect`, ODBC brace-delimited values containing semicolons and
alternative secret parameter spellings/spacing need explicit policy. `rsplit('@')`
is not reliable URL parsing when `@` is in a query value. Do not advertise that
replacement as comprehensive credential protection.

### SEC-02 — SQL value binding is needed, but the migration crosses interfaces

**Evidence:** `core/sqlsource.py:120–192,295–310,344–373`,
`gui/page_monitor.py:324–334`.

`build_query()` removes apostrophes and interpolates start/end as SQL literals.
It accepts an invalid timestamp string. Its identifier regex allows spaces and
SQL fragments: the unchanged function accepted
`table='meters CROSS JOIN other_readable_table'`. The resulting query passed the
real sqlglot read-only parser. This is not evidence of permission escalation:
the editor already permits custom read-only SQL, and database permissions still
apply. It does prove that the current check is not a strict table-identifier
whitelist and can change the query's intended shape/workload.

The real AST validator **does** reject multiple statements and DELETE in the
executed probes. Read-only drivers/session flags and fetch caps are meaningful
existing defenses; they must not be replaced by regex validation.

The submitted parameterized design is the right direction, but cannot be copied
unchanged:

- The GUI currently expects `build_query()` to return text for `setPlainText`.
  A `(sql, params)` tuple changes that contract.
- Actual execution uses `Connection.execute(text(s))`, not `pd.read_sql`.
  Either API supports parameters, but every route must forward them explicitly.
- Generic ODBC calls `cursor.execute(sql)`; it needs driver-appropriate positional
  parameter adaptation, not blindly passing SQLAlchemy `:name` placeholders.
- Persist/query-template and editor state must retain parameters separately.
  Editing SQL must not leave stale parameter values silently attached.
- Validate absence versus empty input explicitly. `pd.Timestamp` is not a strict
  ISO-8601 validator; it accepts broader date inputs, and `NaT`/timezones/type
  mismatches/range reversal require dedicated checks. Never rely on datetime
  validation as the injection defense: **binding is the defense**.
- Validate limits as bounded positive integers; avoid accidentally turning a
  requested zero/invalid limit into an unbounded query.
- Construct/quote identifiers from validated components for the actual dialect.
  Continue `sqlglot.parse` validation of the generated query shape.
- Render pagination by dialect. A universal `LIMIT` is not valid SQL Server syntax.

Acceptance requires SQLite execution plus the relevant MySQL/PostgreSQL/SQL Server
and ODBC adapters, hostile values kept as bound data, genuine identifier rejection,
read-only checks intact, and an end-to-end GUI-generated-query test. No remote
MySQL backslash-escape exploit is claimed from this source review.

### SEC-03 — Required external storage needs fail-closed policy

**Evidence:** `db/session.py:164–218` and its primary/temp/memory fallback ladder.

Fallback is intentional and visible through logs, audit and warning banners; the
word "silent" is inaccurate. It can nevertheless split operational history
between an expected external store and a local file. The in-memory rung also
creates explicit durability risk, already accompanied by critical warnings.

Keep convenient local-first defaults, but introduce an explicit startup policy
such as `external_required` versus `local_allowed`. A stricter organization may
require explicit approval for any fallback rather than only an environment flag.

Do **not** read `EB_DB_STRICT` ad hoc in `db/session.py`. Keep environment/config
resolution centralized in `config/settings.py`, including strict boolean parsing,
and snapshot the resolved policy into diagnostics/provenance without credentials.
The exact existing AST guard protects the named DB URL variables; its not flagging
a new variable would not justify scattering configuration reads.

Handle the engine cache: the current function returns `_engine` before probing.
A later `get_engine(strict_external=True)` must not return an already-cached local
fallback. Prefer immutable startup configuration, or validate cached backend/policy
compatibility. Strict failure must occur **before any fallback writes or migrations**,
produce an English actionable message, and propagate to CLI exit status/GUI blocking
state. Test external success, failure, timeout, cached fallback, environment precedence
and unchanged default offline operation.

### SEC-04 — Credential backend reporting can be false

**Evidence:** `core/secrets.py:15–23,47–70`, `gui/sql_dialog.py:71`,
`gui/page_status.py:284`.

The key-file fallback exists and no explicit user consent/write-disable policy
was found. Creation attempts chmod 0600, so saying it has no permission control
would be wrong. However, creation is write-then-chmod, chmod failures are ignored,
and existing keys are read without repairing/verifying permissions. A key stored
with the encrypted database does not protect against an attacker or backup that
can read both. That does not mean Fernet encryption itself is broken.

A more direct defect: `backend_name()` reports keyring merely if `import keyring`
succeeds. `_fernet()` may subsequently fail to access the daemon and silently use
the key file. The Status/dialog can therefore advertise a backend that is **not
actually protecting the saved secret**.

Resolve an operational backend once and expose its actual backend, persistence,
protection and failure reason. Default to session-only credentials when a secure
store is unavailable; require explicit informed opt-in for a local development
key file. Offer an English warning and a credential-free audit event once per
backend transition, plus a disable-saving control. Use atomic restrictive file
creation and test existing-file permissions if that option remains. Never create
a new master key merely because decryption requested an unavailable old one.
Provide an explicit recovery/rotation/backup policy rather than silently losing
access to stored profiles.

On Windows, `encrypt_text` calls DPAPI directly; it does not implement a cascading
DPAPI-failure-to-Fernet policy. Preserve platform/user binding and do not infer
that a Linux fallback is production-equivalent to Windows DPAPI or macOS Keychain.

### SEC-05 — Additional serialization trust boundary, not covered by the critique

**Evidence:** `core/store.py:73–89,126–134,216–219`,
`lock/baseline_lock.py:260–272,479–481`.

`joblib` is pickle-based executable deserialization. Using it instead of the
`pickle` module is **not** safe loading of arbitrary user-supplied files. SHA-256
checks establish correspondence with a trusted expected digest, not the safety of
a payload or authenticity against someone who can replace both artifact and digest.

Additionally, `_verify_sha256_sidecar_if_present()` verifies only when the sidecar
exists; legacy `load_run`/`load_bundle` can proceed without it. This is weaker than
the locked-baseline loader, which has a mandatory expected-hash contract.

Limit joblib loads to trusted local artifacts. Fail closed on missing required
manifests for supported current artifacts; provide a clearly separate, explicit
legacy migration path. Bind digests to a trust source/path policy and consider
safer supported model formats where feasible. Do not claim that a writable
co-located hash or local hash chain resists a workstation owner rewriting history.
No malicious pickle or arbitrary-code-execution payload was executed in this review.

## 6. Correctness and scientific-validity findings

### SCI-01 — Temperature detection: confirmed bug, incomplete replacement

**Evidence:** `core/features.py:14–22,188–195`; shared callers include
`validation/preprocessing.py:20,136` and `models/portfolio.py:32`.

The original detector classifies `attempt_count`, `template_id`, `temporary`,
`tempo`, and even `boat_count` as temperature-like. Thus this affects more than
one legacy module. HDD/CDD generation further requires successful balance-point
fitting and the degree-day setting; a false name match does not guarantee a
feature will be generated on every dataset.

The supplied `\b...\b` regex avoids those false positives but misses common names
including `temperature_C`, `outdoor_temp_C` and `dry_bulb` in the executed probe.
In Python regex, underscore is a word character, so it does not create the word
boundaries the proposal assumes. The exclusion regex needs the same normalization.

Use explicit schema roles/selection first, then valid temperature units **within
the allowed predictor columns**, then normalized name tokens/aliases. Units alone
cannot decide between outdoor, supply, return, process and setpoint temperature.
The submitted units-first loop can return a column not present in `cols`, or an
excluded supply/setpoint/target column. Detect ambiguity and ask for an explicit
choice rather than selecting the first dictionary entry. Persist the resolved role,
units and decision reason. Keep raw names intact for round-trip prediction.

Acceptance: positive aliases with spaces/underscores/units; all submitted false
positives; role exclusions; competing temperature columns; no suitable column;
Fahrenheit/Kelvin conversion; identical resolved features at prediction time.

### SCI-02 — Global learned preprocessing precedes cross-validation in the desktop path

**Evidence:** `core/pipeline.py:252–300,325`,
`core/features.py:188–244,250–309`, `core/models.py:680–705`.

The actual call order is:

```
cleaned full target data
  -> build_spec(full data, full target)
  -> select_features(full X, full y)
  -> make_folds(...)
  -> evaluate_candidate(already prepared X, ...)
```

`build_spec` fits balance points against the full target, chooses lags/rolls and
interactions from full-data relationships and learns full-data medians.
`select_features` can fit Lasso/MI/correlation selection before outer folds.
`evaluate_candidate` does have training-only median code, which is a positive
control, but cannot undo information already encoded/imputed/selected upstream.

This is source-confirmed leakage risk in the active path. It does not quantify
how much a particular benchmark score is optimistic; that needs a controlled
refit experiment. Deterministic repeated scores are not evidence of unbiased CV.

The separate `pipeline.InFoldModelPipeline` and `run_leakage_safe_cv` implement the
right encapsulation pattern and should become authoritative after integration
qualification. They still import temperature helpers from `core.features`; moving
a file alone will not cure role-detection bugs. Audit upstream missing-value,
category, outlier and calendar preprocessing too, not only the final scaler.

Rolling predictor means are not inherently leakage simply because computed on a
prefix: whether contemporaneous values are available depends on forecasting versus
ex-post baseline use. Learned window selection must remain training-only, and
feature availability/lag alignment must be explicit.

The submitted fixed-threshold account is incomplete: the legacy quality rule uses
0.985 for small n and 0.99 otherwise, with additional name/extreme-correlation
logic (`core/quality.py:729–756`). VSG also includes affine, sibling-target and
rank/correlation screening (`core/vsg.py:350–411`). None can certify the absence
of nonlinear/time/availability leakage.

Required tests: perturb only held-out targets/covariates and assert unchanged
training preprocessing/selection; adversarial nonlinear leaked predictors;
unknown categories and missing data; delayed predictors; causal rolling prefixes;
identical fold contracts through desktop, CLI and SDK. Preserve VSG/TCP acceptance
before inferential execution. A corrected CV path may legitimately change model
rankings; version that change instead of forcing old optimistic numbers to remain.

### SCI-03 — Additional integer-lag contract crash and purge-gap mismatch

**Evidence:** `FeatureSpec.lags/rolls` at `core/features.py:35–38,213–230` versus
`core/pipeline.py:291–298`.

The feature spec stores lists of integers, but the pipeline computes maxima with
`for _, lag_v in spec.lags` (and the same pattern for rolls). Executing that
unchanged assignment with `[1, 7]` raises:

```
TypeError: cannot unpack non-iterable int object
```

Thus nonempty selected/configured lag or rolling lists can fail a target analysis.
The outer handler records a failed target, so this is not a demonstrated complete
application crash. Empty-list fixtures do not exercise it.

Also, `gap = min(p_spec.purge_gap, max(1, n // 5))` can reduce a required gap. The
provenance retains the original spec while the folds get the reduced value. When
history is insufficient, reject the fold configuration or disclose an explicitly
approved alternative; do not claim the required purge was applied after silently
capping it. Add an invariant comparing persisted required/applied gaps and actual
train/validation separations, especially at small n and long rolling windows.

### SCI-04 — Two different scoring semantics and a sensitivity mismatch

**Evidence:** `core/scoring.py:9–16,65–81`,
`scoring/composite.py:16–60,213`, `core/pipeline.py:411–414,536–573`.

The legacy and newer 13-criterion dictionaries share only **five** keys:
`cvrmse`, `nmbe`, `r2`, `parsimony`, `interpretability`. The other eight are different
concepts/aggregations. Importing `DEFAULT_WEIGHTS_13` into the old scorer would
reference missing subscores or silently redefine the meaning of ranking.

The active shortlist is produced by legacy `score_models`; later the code builds
new `subscores_13` and calls `analyze_weight_sensitivity_20pct` without supplying
the active legacy profile. That sensitivity output is not automatically a
sensitivity analysis of the ranking the user selected. In the conversion, the
new `wape` argument receives the existing sMAPE value as a proxy; WAPE and sMAPE
are not interchangeable. These distinctions must be labeled or corrected.

A concrete policy divergence already exists: the legacy R2 threshold is 0.5 for
weekly data, while `validation/ashrae14_metrics.py:58–67` uses 0.60. Monthly is
0.75, not universally 0.75 as the critique implies. Daily/weekly conventions and
interpolation should be labeled engineering policies, not claimed as an exact
universal reading of a standard without authoritative verification.

Select one versioned criterion schema and distinguish **acceptance eligibility**
from **ranking preference**. Map/migrate legacy profiles explicitly and retain
historical score-policy IDs for old runs. Use the same normalized weights,
subscores, qualified candidate set and tie-break policy for ranking, sensitivity,
report and lock rationale. A reported stability result must be bound to that policy.

Custom weights already exist as mapping arguments in composite library functions;
the current desktop configuration exposes only three profiles. Extend the shared
configuration with strict key, finite-number, sign and positive-total validation;
reject unknown criteria rather than silently ignoring them. Current
`normalize_weights_13` ignores unknown keys and clamps negative values, which is
not an ideal contract for user-authored domain policy. Record requested and
normalized weights plus schema/version in provenance. A Pareto view can complement
scoring but should not silently replace current deterministic ranking.

### SCI-05 — HPO has partial budgets, not a global enforceable deadline

**Evidence:** `core/models.py:348–382`, `core/pipeline.py:275–310,681–685`,
`validation/nested_hpo.py:54–175`, and `core/pool.py:235–367` cancellation hooks.

The submitted account omits existing trial limits, data-size adaptations, NN
early stopping and pool cancellation. The newer nested-HPO helper explicitly
accepts `timeout_seconds` (default five seconds) and passes it to Optuna.
The active legacy HPO has trial limits but no timeout. Neither establishes an
end-to-end per-target deadline across preprocessing, all candidates, all folds,
final refit and interpretability. A timeout on `study.optimize` is normally a
soft boundary between work units; it does not interrupt an arbitrary estimator's
ongoing fit. Inner-fold preparation is outside that study timeout too.

Likewise, current target concurrency defaults to at most two targets, not a
simple `CPU//2` count of simultaneous targets. Per-estimator `n_jobs`, BLAS/OpenMP
and neural threads still need a combined oversubscription budget. Runtime in
hours is a plausible concern, not a measured result of this review.

Implement a monotonic target-level budget, remaining-budget propagation and
structured partial-result statuses before adding new tuning algorithms. Expose
cancel/timeout decisions and evaluated/skipped trials in the audit.

Hyperband/ASHA is useful only when estimators provide meaningful intermediate
resource reports and `trial.should_prune` is honored. Adding `HyperbandPruner()`
alone to an objective that returns only a final error is not a solution. RDB resume
requires studies keyed by dataset/fold/domain/feature-policy/objective/seed hashes;
otherwise previous trials can contaminate a different analysis. Coordinate SQLite
storage with the current worker-no-ORM/SQLite rule and central writer policy rather
than bypassing it. Test slow/non-cooperative fits, cancellation, deterministic
replay semantics and safe resumed-study compatibility.

### SCI-06 — Preserve metric safeguards, but do not universalize them

Both legacy and newer metric functions mask near-zero MAPE denominators and use
fallback normalization (`core/models.py:72–89`,
`validation/ashrae14_metrics.py:99–109`). This is a strength. Generic domains still
need explicit treatment of signed/zero-mean targets, constant outcomes, proportions,
counts, denominator units and uncertainty assumptions. Financial returns or defect
rates should not inherit an energy-consumption acceptance formula solely because
all are numeric arrays. No distribution-free conformal guarantee should be made
for arbitrary autocorrelated or shifted series.

## 7. Duplicate hierarchy: what to consolidate and what to retain

| Responsibility | Finding | Safe migration direction |
|---|---|---|
| Models | Legacy candidate/fitted APIs, F1–F10 classes and portfolio wrappers coexist. `f10_stacking.py:11` imports legacy `make_folds`; newer preprocessing imports legacy feature helpers | Extract neutral split/metric/contracts modules first. Choose one evaluator, adapt estimators, then re-export explicit compatible names |
| Scoring | Different schemas, thresholds and active-vs-sensitivity policies | Resolve/version semantics before moving constants; one policy object shared by ranking, sensitivity and reports |
| Mining | `core/mining.py` remains an orchestration module but already calls newer clustering functions | Preserve valid delegation; separate discovery records from model features; no blanket filename-based deletion |
| Reporting | `core/report.py` already adapts to `report/`; `core/report_template.py` only re-exports grammar | Treat this as progress, not another independent renderer. Preserve compatibility unless an actual duplicate path is found |
| Pipelines | Desktop `core.pipeline` and leakage-safe public `pipeline` coexist; CLI exposes newer paths | Desktop/CLI/SDK must call one orchestrator/evaluator with identical fold/preprocessing contracts |

The proposed model facade will not work as written. `models.base` is not a
drop-in source of legacy `Ctx`, `Candidate`, `Fitted`, `Blend`, etc.; `models.top5`
is a representation/attribution module, not the source of the legacy evaluator
functions named in the snippet. There is already a reverse dependency from
`models.f10_stacking` to `core.models`, so eager re-exporting the new package from
the old one risks a circular import before APIs are migrated.

Avoid wildcard exports and import-time warning spam. Specify explicit deprecated
exports, adapter behavior and a removal policy. Deprecation warnings may be hidden
by current warning filters (`pytest.ini`; `core/mining.py:15` globally suppresses
warnings), so add a deliberate development/runtime migration signal where useful.

Introduce dependency-direction contracts with a temporary, reviewed allowlist.
First prohibit **new** legacy imports, then reduce the allowlist. An immediate
blanket ban would break current product code; a token/regex guard alone will not
prove one authoritative evaluation route. Contract tests should execute a small
identical input through desktop/CLI/SDK adapters and compare fold identity,
selected features, metrics, ranking and policy IDs.

## 8. Domain-pack architecture: recommended target and missing contracts

### 8.1 Dependency direction

```
Desktop / CLI / Python SDK adapters (English-only)
                   |
                   v
Typed orchestration + domain-neutral analytics contracts
                   |
            DomainPack protocol
                   ^
                   |
Trusted built-in energy pack; later water/manufacturing packs

Shared services: schema/units, persistence, secrets, process budgets,
provenance, audit and immutable report rendering
```

The kernel must depend on neutral interfaces, never concrete energy modules.
Domain-specific fitted estimators may implement common contracts; keeping F2
energy-specific is valid, but generic orchestration must not require it for every
dataset. F1–F10 are not universal eligibility for every target type.

The initial pack should be built in and offline, with deterministic selection.
Third-party plugin discovery and a marketplace can be added later; they are not
prerequisites for proving an architectural seam.

### 8.2 Extend the proposed protocol before stabilizing it

The supplied `DomainPack` protocol is a useful sketch but lacks:

- `api_version`, pack/version identity, compatibility range and deterministic
  package/resource digest for manifests and baseline locks;
- immutable validated configuration and capability declarations;
- distinction between base metrics, acceptance rules and ranking policy;
- structured decisions with rule ID, inputs, units, result, reason, caveats and
  artifact references, rather than just `(bool, str)`;
- data/target/availability support and explicit model/test applicability;
- train-only `fit` / deterministic `transform` feature-builder lifecycle,
  serialized feature state and leakage constraints;
- safe report-section/chart specifications consumed by the existing `ReportModel`,
  not callbacks that can bypass numeric source binding or invent metrics;
- namespace/duplicate handling for rules, sections, units and entry points;
- missing/incompatible pack behavior on loading historical runs and locked models;
- security/trust policy and offline installation/verification behavior.

Do not assume `entry_points(...)[pack_id].load()()` is a security boundary.
`load()` executes installed Python code with the user's privileges. Define
allowlists/trusted distributions, explicit activation, collision rejection and a
compatible API handshake. Do not auto-download or auto-enable a pack when opening
a dataset/project. A separate process is an isolation aid, not by itself a sandbox.

Persist pack ID/version/digest, unit-registry revision, acceptance/scoring policy
versions, resolved configuration and feature state with run/report/baseline IDs.
A report-only historical view can remain available when compatible re-execution
is unavailable; do not silently substitute whatever version happens to be installed.

### 8.3 What should move, and what should stay generic

Move energy role aliases, HDD/CDD and balance-point policy, energy acceptance
rules, energy narrative additions and F2 applicability into the energy pack.
Retain generic metrics, fold machinery, estimator adapters, test dispatch,
uncertainty infrastructure, budget/cancellation services, provenance and report
renderers in the kernel/services layer. Keep genuinely generic lag, calendar and
rolling machinery generic; its configuration can be pack-specific.

The existing schema already has generic role, quantity and availability concepts
(`core/schema.py:282–320`). Reuse these rather than creating a second role schema.
Separate quantity semantics from unit dimensions: two temperatures can have very
different causal roles even though they share the same physical dimension.

Preserve current energy defaults where scientifically valid. Correcting a leaked
CV score or inconsistent threshold is a versioned behavior correction, not an
energy-regression failure to hide behind an exact legacy snapshot.

### 8.4 Pint: useful extension, not a prerequisite for the first seam

The existing registry is not exclusively kWh. It already distinguishes energy,
power, temperature, volume, volumetric flow and dimensionless values; explicitly
incompatible conversions are rejected (`core/ingest.py:109–128`). It supports
affine Celsius/Fahrenheit/Kelvin conversion. The critique's claim of a single
canonical energy unit for all inputs is incorrect.

Its extensibility is limited (39 aliases), and unknown units are currently left
unchanged with a log record (`core/ingest.py:138–141`). Generalization needs a
clear reject/warn/explicit-unit policy.

Start with a neutral UnitService interface and compatibility tests, then put Pint
behind it if needed. No need to add `pint_xarray` just to convert pandas columns.
Check absolute versus delta temperatures, degree-day integration, flow versus
interval totals, offset units, aliases, precision, dimensionless KPI semantics and
unit-registry persistence. Currency conversion needs dated/contextual exchange-rate
policy; plain dimensional arithmetic is insufficient. Do not silently let packs
redefine one another's units. Pin/license/hash any new dependency before shipping.

## 9. Stages, typed results and failure semantics

A Stage protocol is appropriate if it exposes a small useful contract, not a new
layer of orchestration around the existing monolith. Suggested boundaries are:

1. configuration/domain identity and storage policy resolution;
2. ingestion/schema/time and information-availability validation;
3. descriptive quality/discovery and explicit VSG/TCP acceptance;
4. fold construction and train-only preprocessing/evaluation;
5. acceptance, ranking, sensitivity and candidate register;
6. baseline lock and uncertainty calibration;
7. advisory monitoring/drift and immutable snapshot binding;
8. report-model assembly and pure exports.

Test applicability has pre-fit and post-fit components; it is not accurately
represented by moving every T1–T14 test after UQ in a linear diagram. Preserve the
accepted gate dependencies and do not allow a plugin/SDK shortcut to run inferential
tests before acceptance.

Use typed nested results for boundaries first: `FoldPlan`, `FeatureState`,
`CandidateEvaluation`, `AcceptanceDecision`, `RankingPolicy`, `StageIssue`, and
`RunManifest`. A stage result should distinguish completed/skipped/failed/cancelled/
timed-out, carry typed artifacts and structured issues, and identify upstream
inputs/hashes. Separate runtime estimator handles from JSON-serializable DTOs.

Pydantic v2 can improve validation but does not make schema versioning, migrations,
semantic correctness or arbitrary estimator serialization free. Define explicit
schema versions/migrations and strictness. `frozen=True` alone does not deeply
freeze nested dictionaries. Keep the existing immutable report JSON boundary;
there is no reason to replace it wholesale merely to use Pydantic everywhere.

Treat broad exceptions by boundary:

- catch expected dependency/import/data failures narrowly;
- preserve rollback/re-raise and best-effort cleanup patterns where appropriate;
- report an explicit skipped optional feature with a stable event ID;
- keep unexpected exceptions observable without credential-bearing payloads;
- do not log the same traceback at every layer;
- turn on `BLE001` incrementally with justified local exceptions, not blanket
  per-file ignores that hide new catches.

`log.debug` alone is insufficient for a feature change a production user must
understand: INFO-level default logging hides it. Emit a structured issue in the
run/report/AUTO log, and use a suitable operator-facing warning for material
changes. Existing global warning suppression also deserves targeted reduction.

## 10. Development, platform and language policy

### Tests and structural gates

`guard_tests_integrity` protects **four** baseline files, not every test and not
all implementation structure. It exists to prevent changing acceptance assertions
to make code green. Do not remove it or regenerate hashes to bypass a refactor.
Add adapter/behavior tests and obtain explicit approval if frozen contract changes
are genuinely needed. Preserve no-web, SQL-AST, no-QThread-compute and environment
reader guards while refining implementation-detail assertions individually.

Use property/metamorphic tests for fold isolation, unit reversibility, ranking
invariance under weight scaling, archive integrity and artifact/numeric agreement.
Use import contracts for dependency direction. Neither kind replaces the other.

### Dependency manifests

Three files are defensible if roles are explicit: direct intent, generated resolved
view, and authoritative hash-locked installation output. Current versions agree
under the comparison described above. The maintainability gap is the absence of a
single clearly enforced regeneration/check workflow, not proof that uv would solve
a runtime defect.

Choose one generator, check reproducibility and marker/version consistency, and
keep installers/CI/audit/SBOM/build scripts aligned. If uv is adopted, distinguish
`uv.lock` project locking from `uv pip compile --generate-hashes` requirements
output; they are not the same file format or command result. Windows/Linux/optional
profiles and offline installs must survive the migration. Deleting
`requirements.txt` immediately would break assumptions in tooling and structural
tests and is not recommended as a first step.

### Metadata and security-documentation drift

`energybaseline/__init__.py` reports `__version__ = "3.0.0"`, while
`pyproject.toml` declares `6.0.0.dev0`. This can make health/provenance output
misleading even when the commit hash is correct. Choose one generated/authoritative
version source and test installed-package versus runtime version agreement.
`SECURITY.md` also mixes old baseline descriptions and future controls; its claim
that passwords are excluded from logs is contradicted by SEC-01. Update those
claims alongside remediation rather than treating documentation as evidence of an
implemented control.

### Python and operating-system support

The 3.11-only package metadata is real. End-of-life in October 2027 is a lifecycle
planning issue, not proof of an immediate unsupported runtime. Moreover, the pinned
Windows 3.11.9 build and Linux 3.11.18 build have different patch histories; binary
runtime security servicing deserves attention before broad version claims.
No compatibility experiment here establishes that torch/pandas block 3.12/3.13.

Add experimental 3.12/3.13 jobs for numerical contracts and dependency resolution,
then real GUI, serialization/migration, R parity, lock generation and packaging
qualification before changing the supported range. Keep a supported baseline while
that work proceeds. Newer Python's import/process/deserialization behavior can
matter even if a wheel installs successfully.

Windows support is deliberately primary. `pywin32` is platform-marked, so its
presence does not mean unconditional installation on Linux. Linux CI is not Linux
desktop production acceptance. macOS requires Keychain capability tests, GUI/DPI,
spawn/cancellation, filesystem/bundle paths, architecture-specific dependencies,
notarization/signing, backup/restore and real packaging—not simply another matrix
row. `SUPPORT_MATRIX.md` also contains drift: its Ubuntu OS row still lists
22.04/24.04 while workflow/runtime notes now refer to 26.04.

The earlier nightly Python runtime fix is already present at the reviewed commit:
`r-reference.yml` pins Ubuntu 26.04 / Python 3.11.18 and uses Node-24 generations
of the three named actions. It is not an outstanding unimplemented fix here.
The main CI still has older first-party action majors; that is a separate workflow
maintenance item, not this nightly failure recurring in unchanged code.

### Auditor alias

The root alias delegates unchanged to `pip_audit`; it is not a fake scanner and
was explicitly documented for an earlier gate-command spelling. Prefer canonical
`python -m pip_audit` in all callers and documentation, then retire the alias under
a compatibility decision. Merely moving it to `scripts/` breaks
`python -m pip-audit` from the root. Do not weaken auditing to make that migration.

### English-only versus Unicode data rendering

Keep English UI/report/logs/tests/docs and current layout direction. No QTranslator,
Persian translation files, RTL QSS, `arabic_reshaper` or bidi dependency is required
for the requested product direction. International users can use English software.

The PDF renderer uses Helvetica/default styles (`report/pdf.py:34,53–57,114`).
It does not configure a broad Unicode font or shaping. This limits non-Latin user
labels/data even while generated prose is English. If such data must be supported,
add a licensed embedded font and character-coverage tests independently of product
translation. RTL shaping becomes relevant only if RTL **data** is accepted as a
requirement. Do not describe all non-ASCII characters as broken—some Western
symbols are supported, and actual repertoire needs tests.

The existing report grammar is shared structured Python data, not a reason to
switch every renderer to Jinja2. Templates must preserve escaping, artifact IDs,
number validation and JSON-only renderer boundaries. Jinja2 being in a lockfile
is not an architectural requirement or a PDF layout engine. Retain ReportLab as
the only PDF generation engine.

## 11. SDK, server, enterprise features and candidate domains

A stable Python SDK is the best next delivery abstraction **after** evaluator
unification. There are already importable public Python functions, but that is not
a versioned, lightweight, dependency-declared headless SDK contract. The current
package metadata and monolithic runtime requirements need deliberate packaging
work. Preserve argparse unless a concrete CLI usability requirement justifies
Typer; framework replacement does not generalize the analytics.

An SDK has lower operational complexity than a server, **not zero security risk**.
It can load unsafe artifacts, execute plugins, access SQL and leak sensitive data
through exports. Design the trust boundary explicitly.

Do not add REST/FastAPI or relax the desktop no-web guard in this repository as a
side effect of generalization. A future separately packaged/deployed opt-in service
would need its own threat model, authentication/authorization, tenant isolation,
TLS, job quotas, data retention, network policy and audit/approval controls. Its
desktop integration must be opt-in and must not compromise default offline use.
A different repository/package is a legitimate later choice, not an existing
requirement fulfilled by creating an empty server facade.

Multi-user and two-person approval are separate enterprise product commitments.
A local override, a hash chain, or a signature-looking GUI field is not authenticated
dual approval. Regulated/GxP operation additionally needs identity/lifecycle policy,
access controls, durable/tamper-resistant records, trustworthy time, retention,
validation evidence, recovery and operator procedures. Installing a pharma domain
pack alone cannot establish 21 CFR Part 11/ALCOA+ compliance.

| Proposed domain | Assessment of additional contract/validation needs |
|---|---|
| Water/wastewater | Good first contrasting pack: units and interval totals, rainfall/time availability, dry/wet applicability, domain acceptance; not merely energy metrics relabeled m3 |
| Manufacturing/OEE | Good second proof: batch/group CV, operating state, bounded ratios/counts, aggregation/changeover semantics and appropriate charts; OEE decomposition must be defined |
| Finance/risk | Heavy-tail/time ordering/backtesting and loss-function differences; explicit financial acceptance and monetary context, not ASHRAE scoring; library availability is not validation |
| Marketing/MMM | Adstock/saturation can leak through whole-data fitting; causal/attribution claims need identification and interventions, not just predictive fit |
| Agriculture | GDD/ET0 and phenology need correct units, calendars, season/region validation and information availability; HDD/CDD code is only an analogy |
| Fleet | Fuel/electric quantities, payload-distance normalization, duty cycles and group/time split validation |
| Health | De-identification/minimized exports plus access/retention/privacy policy; a single hook is not privacy compliance |
| Pharma/GxP | Start with requirements/threat model and validation plan; do not promise regulatory compliance from an analytics plugin |

## 12. Revised roadmap, release gates and effort assessment

The suggested P0–P3 headings are useful, but the quoted durations and "four files,
less than a sprint" are not justified. Binding changes touch query generation,
execution, ODBC, UI state, persistence and tests. Pipeline/scoring migration changes
scientific outputs and archived-run compatibility. No reliable calendar commitment
can be made without staffing, supported-domain decisions and performance targets.

Use these exit gates instead:

| Phase | Scope | Required exit evidence |
|---|---|---|
| **P0 — trust and correctness** | All secret sinks; actual backend policy; parameterized SQL across routes; explicit external-storage policy; temperature/lag/purge defects; immediate containment of globally learned CV inputs | Canary-free logs/audit/status; no fallback writes in strict mode; real adapter binding tests; lag/rolling regression cases; held-out perturbation tests. Review archived log exposure and rotate credentials if actual leakage is found |
| **P1 — one analytical truth** | One fold/evaluation path and scoring policy; explicit compatibility facades; typed stage boundaries; dependency-direction contracts; shared unit/role service | Same scientific contracts through desktop/CLI; sensitivity bound to active weights; known energy acceptance decisions preserved or explicitly version-migrated; no new legacy imports |
| **P2 — prove generalization** | Built-in energy pack plus genuinely contrasting water/manufacturing pack(s); stable Python SDK; versioned plugin/unit/report extension contracts | Run non-energy workflows without modifying kernel source; no energy thresholds/degree days in generic runs; pack/version reproduction; offline SDK packaging and historical-run load tests |
| **P3 — optional product expansion** | External pack ecosystem, more platforms, separate server/enterprise approvals if approved | Security and support qualification for each new delivery surface. English remains the language unless the owner later changes policy |

Do not put all of P0's safety work behind P1's package rearrangement. Conversely,
full model/scoring unification is not a trivial security hotfix: plan it as P1,
with narrowly scoped P0 scientific containment. Installable marketplaces, regulated
packs and macOS/server support should not block a reliable English desktop/SDK.

### Suggested immediate work packages

1. **P0-A, security owner:** safe connection diagnostics, exception sanitization,
   actual backend capability reporting, credential-persistence policy and missing
   artifact-integrity handling. Keep synthetic canary tests private to normal
   engineering review; never publish actual credentials in an issue.
2. **P0-B, data-platform owner:** `QuerySpec`/parameters, driver adapters, timestamp
   and identifier contracts, explicit strict-storage startup policy.
3. **P0-C, statistical owner:** lag/rolling shape fix, honest purge enforcement,
   train-only preprocessing on the active route and a versioned correction plan.
4. **P1-A, architecture/statistics joint owner:** neutral split/metric contracts,
   one evaluator and one acceptance/ranking schema; sensitivity consistency.
5. **P1-B/P2, product owner:** approve the minimum DomainPack/SDK contract and choose
   one contrasting second domain before adding an ecosystem or server roadmap.

These are roles, not a claim that the repository has those staff assigned.

## 13. Competitive comparisons and product positioning

No claim of superiority over Metrix, RETScreen, EnergyCAP, OpenEEmeter, SkySpark,
KNIME, Orange, DataRobot, H2O, JASP/jamovi, Grafana/Evidently/WhyLabs or MLflow/DVC
can be established from this repository review. Statements such as "stronger than
all of them", "they have no UQ/audit" or "this market is empty" should be removed
from externally facing claims until verified against named product versions,
license tiers and dated official documentation/benchmarks.

Architectural analogies remain useful: extension APIs from modular desktop tools,
SDK separation from scientific libraries, and experiment/model registry concepts
from lifecycle tools. They are **design references**, not proof of equivalent
maturity or a feature advantage. A baseline lock and an experiment registry solve
overlapping but different problems; neither is automatically stronger overall.

Build a comparison matrix using the same dimensions and evidence per product:
offline operation, raw-data privacy, model/test applicability, leakage-safe temporal
validation, empirical interval calibration, artifact authenticity, workflow approval,
connectors, usability, supported platforms, maintenance and total operating cost.
Run identical datasets/tasks where feasible and distinguish product capability
from this implementation's actual verified path.

The proposed positioning—an offline-first, auditable baseline/deviation workbench—
is a coherent **hypothesis**. The statement that 70% of the engine is reusable is
also a hypothesis. Measure reuse and kernel-change count during the second-pack
exercise rather than using an unsupported percentage to justify scope or dates.

## 14. Verification performed and recommended next decision

Reproduce the isolated probes from the repository root with Python 3.11 and
`sqlglot==30.31.0` installed:

```bash
python artifacts/architecture_review/review_probe.py
```

The probe regenerates `inventory.json`, `probe_results.json` and a console summary.
Its source definitions are extracted from the current checkout, so compare its
recorded source commit before interpreting results on a later revision.
`SHA256SUMS.json` records the delivered review-file hashes and reviewed commit
object hash; it is an integrity manifest, not a signature.

Executed during this review:

- isolated source probes for credential masking, temperature detection, scoring
  schema differences, the integer-lag expression and actual SQL AST/query building;
- a source/AST inventory with explicit count scope;
- `guard_web_free.py`, `guard_env_readers.py`, `guard_tests_integrity.py`,
  `guard_no_qthread_compute.py`: passed;
- `gen_matrix.py --check`: passed, 251 IDs / 188 marked done;
- lint/format validation of the review probe only.

The static gates passing does not refute the findings: most enforce useful
architecture/integrity contracts, not all runtime confidentiality/scientific
properties. Historical requirement statuses and Phase 8 evidence were not changed
or re-certified. Newly found gaps should be formally triaged against those claims
rather than hidden by a green matrix.

**Recommended owner decision:** authorize small P0 security/correctness changes
with explicit regression tests, followed by a separate P1 evaluator/scoring
consolidation proposal. Retain the offline desktop, ReportLab-only rendering,
English-only language and existing test-integrity guarantees. Defer localization,
marketplace, server, macOS production support and regulatory claims until they are
separately justified and accepted.

The product's strongest path forward is not "replace everything with plugins".
It is **one trustworthy analytical execution path, explicit domain policy, and
thin delivery adapters**, expanded only when a second real domain proves that
separation works.
