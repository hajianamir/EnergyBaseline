# Preregistered statistical plans and business assertions

## Scope and verification

Stage 3 adds 27 explicit statistical method identifiers through a separate gated API, CLI and native Setup tab, plus a twentieth descriptive workbench operation, `business-validation`. It does **not** complete the whole capability backlog. Execution status and actual Windows/Linux receipts are recorded in `artifacts/capabilities/statistics-stage3/README.md`; source review alone is not execution evidence. No dependency or historical test/coverage threshold is removed.

## Authorization, not flags

```python
from energybaseline.statistics import run_statistical_plan

# Use an actual VSG accepted for this exact frame/time/target/schema.
# Declare categorical design metadata as GROUP roles before accepting VSG.
# Use a mutable TCP bound to that same accepted VSG.
tcp.register_extension_plan(plan)  # validates structure and binds the exact JSON digest
tcp.lock_preregistration()  # explicit decision by the caller, before computation
result = run_statistical_plan(plan, frame, vsg=vsg, tcp=tcp)
```

Missing or changed VSG, data, targets, schema, accepted variable selection, plan, or TCP lock blocks execution. Registration invalidates TCP acceptance. Editing even one parameter requires an explicitly audited amendment and a new lock. A what-if clone does not inherit plan registrations or authority. JSON `accepted`/`locked` flags cannot substitute for live gate objects. Checks repeat after computing the complete family, before any evidence is returned. No result is released when a later procedure fails.

Only plan digests, not plan literals, are added to TCP persistence/provenance. The empty extension field is omitted to preserve historical canonical hashes. Existing `save_tcp_config`/`load_tcp_config` retain and integrity-check these digests. Reloaded dictionaries are records, **not** executable authorization: rebuild/accept VSG, reload the caller-held plan, register and explicitly lock TCP again. Plan literals, raw input data and reference tables are not automatically added to `.ebproj`.

Plans require version 1; explicit `time`, unique `targets` and `metadata`; `sampling`; an assumption justification; a correction policy; and 1–50 uniquely identified procedures. Each procedure declares `id`, `method`, ordered `columns`, and method-specific `parameters`. See the executable JSON plans in `examples/statistics`. There are no executable user formulas, user imports, model downloads or pickle-file interfaces. Only fixed internal formula aliases reach the design library.

The native **Setup → Preregistered Statistics** tab exposes a JSON editor and separate **Register Exact Plan Before Lock** / **Run Preregistered Plan** actions. Prepare roles and accept VSG in Setup, register the loaded plan while TCP is mutable, explicitly lock TCP in Setup, then run. Computation uses the existing allowlisted spawn-process pool, not the GUI thread. Editing inputs, configuration or selection invalidates displayed/in-flight evidence; completion rechecks the current live lock as well as input/plan hashes. Cancellation uses the existing cooperative-boundary/hard-stop pool lifecycle.

## CLI

```bash
python -m energybaseline.cli statistical-plan examples/statistics/independent.json \
  examples/statistics/input.csv --accept-and-lock --out statistical-result.json
```

`--accept-and-lock` is an explicit interactive-equivalent decision for **this invocation and exact plan/input**. It does not bypass core authorization: the CLI constructs actual VSG, registers the plan and locks TCP before calling the runner. Without it, exit 2 and no inference. The CLI supports `--alpha` and the nine existing TCP correction choices. It does not load a persisted boolean as permission. Invalid method/design/numerical execution returns a bounded generic error without raw observation values or partial results. Exit 0 means execution succeeded, not that any null hypothesis was rejected or a model was certified.

The independent, paired, Bayesian, prospective and two-stage-FDR plans cover every method identifier on a small synthetic dataset. Assumptions in these examples are not certificates for real data. All results include input/plan/VSG/preregistration digests, library versions and an evidence digest; no baseline is changed.

## Statistical procedures and limits

| Methods | Column order / parameters | Meaning and limits |
|---|---|---|
| `ks_fixed_normal`, `lilliefors` | one numeric column; KS `location`, `scale`; Lilliefors no parameters | Ordinary one-sample KS uses externally specified normal parameters. Lilliefors fits mean/variance and uses its own tabulated calibration, including endpoint bounds. Neither is a KS drift replacement or an autocorrelation guarantee. |
| `one_sample_t` | one numeric column, `null`, optional `alternative` | Mean-minus-null estimate and shifted t interval; directional unbounded endpoints are explicit nulls with flags. |
| `pooled_t`, `welch_t` | response, group; explicit ordered `levels`, optional `alternative` | Equal-variance pooled or unequal-variance Welch test, respectively; first-minus-second mean effect and interval. Constant groups are rejected. |
| `paired_t`, `wilcoxon`, `sign_test` | two aligned numeric columns, optional `alternative` | Independent subjects required. Paired t uses difference-normality assumptions; Wilcoxon reports zero count and assumes symmetric differences; sign test conditions on nonzero differences and gives an exact binomial interval. The Wilcoxon median is descriptive, not a Hodges–Lehmann interval. |
| `pearson`, `spearman`, `kendall` | two numeric columns; rank methods optionally `bootstrap_replicates`, `seed` | Pearson uses the bivariate-normal Fisher interval. Rank intervals are paired percentile bootstraps for independent pairs; standard library rank p-values are not exact for every tie/small-sample setting. Undefined resamples suppress the interval rather than biasing it by dropping draws. Rank intervals support two-sided plans only. |
| `chi_square`, `fisher_exact` | two categorical / binary columns; optional chi-square `yates` or Fisher `alternative` | Chi-square rejects expected counts below five. Fisher requires binary nonempty margins. Infinite sample odds are `null` with an explicit infinity flag, never a fabricated zero estimate. |
| `mcnemar`, `cochran_q` | two / 3–10 aligned binary conditions | Exact McNemar and asymptotic Cochran Q on independent matched subjects, not independent groups. Degenerate undefined statistics fail closed. |
| `factorial_anova` | response, factor A, factor B | Two-way fixed-effects Type II ANOVA including interaction, explicit cell/rank/sample guards. Main effects need caution with interaction. |
| `repeated_anova` | response, subject, condition | Balanced complete one-within-factor design, one observation per subject/condition; at most 100 subjects and 500 observations. Sphericity is assumed, not automatically corrected. No mixed-effects, missing-cell or Greenhouse–Geisser implementation is claimed. |
| `ancova` | response, group, 1–6 covariates | Fixed common slopes and homogeneous independent errors; rank/sample guards. Not an automatic causal adjustment. |
| `manova` | dependent variables followed by group; `dependent_count` | Wilks-lambda group test with full-rank residual covariance. Normality/equal-covariance conditions are caller responsibilities. |
| `tukey_hsd`, `dunn` | response, group | Expanded pairwise comparisons, first-observed group ordering; Tukey simultaneous intervals/internal adjustment; Dunn pooled tie-corrected rank variance and normal calibration. No raw labels/observations are exported. |
| `prospective_power` | no observed columns; `design`, externally specified `effect_size`, `mode`; `power` or `n_per_group` | One-sample t, independent t/z, balanced ANOVA. Integer sample sizes, attained-power check and explicit allocation ratio/group count. No observed-effect estimation or retrospective observed-power mode. Effects are standardized design inputs, not inferred treatment effects. |
| `beta_binomial`, `normal_mean` | one binary / numeric column, explicit priors and threshold; normal mean also known `sigma` | Analytic posterior moments, equal-tailed credible intervals and posterior threshold probability; not frequentist p-values. Known noise is never estimated silently. |
| `normal_model_average` | one numeric column; common known `sigma`, 2–20 normal prior models and prior model weights | Actual marginal-likelihood weights, mixture variance including between-model uncertainty, numerically inverted mixture credible interval. Bounded normal-mean prior-model averaging, not general architecture search/BMA. |
| `linear_metropolis` | response then 1–9 numeric predictors; known `sigma`, coefficient `prior_mean`, `prior_sd`, `proposal_sd`, explicit `seed`; optional `draws`, `warmup`, `chains` | Genuine multichain fixed random-walk Metropolis using the Gaussian posterior density; warmup discarded, no draws persisted. Classical split-Rhat, initial-positive/monotone ESS, acceptance and diagnostic-warning status. These are not rank-normalized/folded modern diagnostics or a universal convergence proof. |
| `linear_variational` | same linear/prior columns; optional `max_iterations`, no MCMC options | Genuine mean-field reverse-KL numerical optimization with gradient, optimizer/bound diagnostics and KL to the known Gaussian solution. Independent factors can understate correlated posterior uncertainty. No general PyMC/Stan/NumPyro integration is claimed. |

### Families, intervals and assumptions

All expanded frequentist rows in one plan form one correction family, including all factorial terms and pairwise comparisons. The existing TCP correction applies when `correction` is `tcp`; `fdr_tsbh` and `fdr_tsbky` explicitly select single two-stage (not iterated-to-convergence) FDR at the locked TCP alpha. Their adjusted p-values are alpha dependent and may be below raw inputs. They are not added to legacy alpha-independent what-if caches. Bayesian/prospective rows are separate and never passed to multiplicity correction as p-values.

Tukey p-values are already internally adjusted and are labeled as such before an additional conservative outer adjustment; `p_input` must not be mistaken for an unadjusted Tukey p-value. Generic output intervals are pointwise, **not** automatically simultaneous with the outer family correction; Tukey intervals are simultaneous only within their own Tukey family. Multiple separately registered plans do not automatically combine into one global family. Preregister that larger family together if needed.

Accepting a gate does not establish independence, randomization, normality, sphericity, model adequacy, causal identification or prior appropriateness. These procedures do not claim distribution-free validity for autocorrelated series. Plans are bounded to 20,000 rows, 50 procedures and 1,000 expanded comparisons, with additional design/bootstrap/MCMC cell/work limits. Observations are not silently deleted/imputed. Sampling `independent_subjects` is reserved for subject-level paired/repeated procedures (and pair associations); other observed-data methods require independent rows. Temporal sorting/uniqueness is checked, not repaired.

## Declarative business validation

The descriptive `business-validation` workbench operation runs before inferential gates because it neither tests a statistical hypothesis nor estimates model uncertainty. It uses `parameters.rules`, each with a short unique `id` and typed `assert` expression. Leaves are `{"column": "name"}` or a finite scalar `{"literal": ...}`. Supported operators: `add`, `subtract`, `multiply`, `divide`, `absolute`; `eq`, `ne`, `lt`, `le`, `gt`, `ge`; `close` with explicit nonnegative `atol`; `all`, `any`, `not`, `implies`; `is_missing`, `is_finite`; and explicit composite `in_reference` keys. No Python/SQL evaluation, callbacks or automatic repairs.

```bash
python -m energybaseline.cli workbench examples/workbench/business-validation.json \
  --dataset examples/workbench/prediction.csv --out business-result.json
```

An optional reference table is caller-bound and hashed; key dimensions and dtypes must match, and reference null keys are rejected. Missing comparison truth fails the assertion and increments the unknown count; boolean composition follows three-valued Kleene semantics. Division by zero yields unknown rather than an invented value. Arithmetic rejects non-real/boolean inputs, integer precision loss above 2**53, infinities and overflow; exact same-integer comparisons preserve large signed/unsigned values without float conversion. Mixed large-integer/float comparisons fail closed.

Outputs contain passed/failed/unknown counts and at most 20 zero-based failed positions per rule, not raw values, input index labels, reference keys or policy literals. There are 50-rule, 16-depth, 500-node two-million row-rule and twenty-million row-node evaluation bounds. This is a custom bounded policy engine, **not** Pandera or Great Expectations integration or unrestricted arbitrary Python validation. Native exploratory workbench and trusted worker routing are shared with the other descriptive operations.

## Remaining program

Backlog fields distinguish this bounded implementation from the original broader requests. Advanced mixed/repeated models, arbitrary Bayesian programs/backends, external validation frameworks, neural/foundation forecasting, vision, larger AutoML/NAS, routing/scheduling, domain simulation, further fairness/stress and explanation families are not made complete by this stage. Report renderers remain ReportModel-only; these outputs are not silently injected into baseline reports or monitoring state.
