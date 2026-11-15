# Capability usage audit: UAP catalog versus EnergyBaseline

Date: 2026-11-12. Baseline inspected: `c5f32177dd699804381c3d8a19bf6b19bb9037ee` (main / PR #9), with the VSG synchronization correction in this branch.

## Scope and interpretation

This is a source-level usage audit, not a benchmark or a claim that every algorithm was executed on a user's dataset. The product package is `energybaseline`, not `uap`. No production `uap` integration was found. The absence of a class with a UAP name does **not** imply the absence of an equivalent algorithm.

- **Integrated**: an implementation has an identifiable application execution path. It may still be disabled by settings, sample-size rules, integrity gates, or missing optional backends.
- **Partial**: a narrower equivalent exists, but the requested general capability or full interface is not established.
- **Library/CLI**: implemented outside the primary desktop analysis path; do not advertise it as automatically used by the GUI.
- **Not found**: no corresponding production implementation/call path was found in this checkout. This is not a statement about what an installed third-party library could theoretically provide.

No capability-use percentage is reported: counting a metric, a model family, a dependency, and an end-to-end subsystem as equally sized features would be misleading. Shared terms such as “balance,” “confidence,” or “optimization” are not evidence of fairness, Bayesian inference, or a general optimization product.

## 1. Data quality, validation, and cleaning

| Requested capability | Finding | Evidence and limits |
|---|---|---|
| Six-dimensional data quality framework | Partial | `core/quality.py::quality_score` exposes completeness, validity, uniqueness, regularity. The weighted score is 40/20/10/30 percent respectively. No six-axis accuracy/consistency/timeliness framework is established by this score. |
| Completeness / missingness profiles | Integrated | `core/quality.py::profile`, `preprocess_target`; `core/pipeline.py::analyze_target`. |
| Uniqueness / duplicate handling | Integrated | `core/ingest.py`, `core/temporal.py`, `core/quality.py`; timestamp duplicates and temporal grid handling are not a general relational entity-resolution system. |
| Accuracy against an external source of truth | Not found | Range checks, outliers, and plausibility scores do not establish measurement accuracy against a verified reference. |
| General cross-column / cross-table consistency rules | Partial | `core/schema.py`, `core/integrity.py`, temporal quantity semantics and leakage rules; no general-purpose rule DSL found. |
| Freshness / SLA timeliness scoring | Not found | Gap/regularity detection is present; that is not a wall-clock freshness SLA engine. |
| Typed schema, roles, units, availability, temporal validation | Integrated | `core/schema.py::DatasetSchema`, `gui/pages_setup.py::build_schema_from_ui`, `core/temporal.py`. |
| Arbitrary business validation suite / Pandera or Great Expectations execution | Not found | Product schema/integrity validation is custom and narrower. Dependency presence would not establish execution. |
| Median, KNN, MICE predictor imputation | Integrated | `core/quality.py::apply_leak_free_missing_policy`; training rows constrain statistical fitting. In-fold evaluation has its own `validation/preprocessing.py` implementation. |
| Linear interpolation / forward fill / missing flags | Integrated, policy-limited | `core/quality.py`; do not infer that every missing-data policy is used in every evaluation path. |
| Target imputation | Intentionally prohibited | Missing targets cannot be invented as observed training outcomes. |
| Mean/mode/general categorical imputation APIs | Not found as a general catalog | The predictor-specific policies above are not a complete arbitrary tabular imputation framework. |
| IQR, MAD / robust z-score outlier detection | Integrated | `core/quality.py::detect_outliers`; robust z-score uses robust bounds, not the ordinary mean/standard-deviation z-score. |
| Isolation Forest, LOF, One-Class SVM, Mahalanobis, HBOS | Integrated | `core/quality.py::detect_outliers`; `core/schema.py` settings; additional descriptive mining in `core/mining.py`. |
| Autoencoder anomaly detection | Integrated, narrow | `detect_outliers('autoencoder', ...)` uses an sklearn MLP reconstruction network; not a general deep anomaly model zoo. |
| Winsorization, flags, predictor interpolation, row removal | Integrated, policy-limited | `core/quality.py`, outlier policy/settings in `core/schema.py`; not unconditional deletion. |
| Learned string normalization, fuzzy deduplication, entity resolution | Not found | File ingestion and column-name handling are not these capabilities. |

## 2. Statistical tests and multiplicity

| Requested capability | Finding | Evidence and limits |
|---|---|---|
| Shapiro-Wilk, D'Agostino K², Anderson-Darling, Jarque-Bera | Integrated / contract-specific | `tests_catalog/t4_normality.py`; sample-size selection and supplementary statistics differ by test. Do not promise every test has the same p-value/CI interface. |
| KS two-sample test | Library/CLI monitoring | `monitoring/drift.py::covariate_drift`, `cli.py` reporting path. Not a universal normality test menu. |
| One-sample KS / Lilliefors normality | Not found | KS drift is not a substitute for these normality tests. |
| Welch independent t-test, Mann-Whitney, Brunner-Munzel | Integrated in specific diagnostics | `tests_catalog/t14_causal_group.py`; Welch/Mann-Whitney primitives also in `core/cache.py`. Not an unrestricted test-builder UI. |
| Ordinary pooled two-sample t, one-sample t, paired t, Wilcoxon signed-rank, sign test | Not found as dedicated general tests | A t-based confidence interval is not evidence of these separate test APIs. |
| One-way ANOVA, Welch ANOVA, Kruskal-Wallis, Levene | Implemented, narrower group-comparison path | `tests_catalog/t14_causal_group.py::compute_multi_group_anova_kruskal`; this helper's existence does not make a full multi-factor/repeated-measures GUI. |
| Friedman / Nemenyi | Integrated model-comparison diagnostics | `tests_catalog/t11_nemenyi.py`. |
| Repeated-measures ANOVA, two-way/factorial ANOVA, ANCOVA, MANOVA, Tukey HSD, Dunn | Not found | Do not equate Nemenyi with every post-hoc procedure. |
| Pearson / Spearman / Kendall associations | Partial | Descriptive coefficients and MI in `core/eda.py`, Kendall inference is gated, Pearson p-values in `core/cache.py`; not one uniform correlation API with all alternatives and CIs. |
| Chi-square contingency, Fisher exact, McNemar, Cochran Q | Not found | A chi-square distribution used for Mahalanobis thresholds or another diagnostic does not implement contingency testing. |
| Stationarity/trend, seasonality, autocorrelation | Integrated | T1–T3 under `tests_catalog`; inferential execution is gated. |
| Heteroskedasticity, VIF, Chow, Quandt-Andrews, Pettitt | Integrated | T5–T9; these exceed a simple generic hypothesis-testing menu. |
| Diebold-Mariano, Little MCAR, influence/outlier diagnostics | Integrated | T10, T12, T13 respectively. |
| Bonferroni, Holm, Benjamini-Hochberg, Benjamini-Yekutieli, no correction | Integrated | `core/tcp.py` correction dispatch and raw/adjusted p-value records. |
| Sidak, Holm-Sidak, Hochberg, Hommel, two-stage FDR | Not found | Not implied by using statsmodels elsewhere. |
| Effect sizes and confidence intervals | Integrated, test-specific | `tests_catalog/tc_assumptions.py`, `ci_method.py`, T1–T14; not all possible effect-size estimators. |
| General power/sample-size design workbench | Partial at most | Assumption/sample-size gates exist; no general prospective power-analysis interface established. Retrospective observed power is explicitly rejected by test contracts. |

## 3. Uncertainty, risk, monitoring, fairness, stress, and validation

| Requested capability | Finding | Evidence and limits |
|---|---|---|
| Parametric regression intervals | Library/CLI | `uq/parametric.py`; not proof of a universal distribution-modeling engine. |
| Split/cross conformal, jackknife-after-bootstrap, CQR | Library/CLI | `uq/conformal.py`, `uq/__init__.py`, `cli.py` UQ command. Exchangeable-data methods are distinguished from sequential methods. |
| EnbPI, adaptive conformal inference | Library/CLI | Same adapter; sequential calibration updates do not refit the locked estimator. Autocorrelated-series coverage is empirical, **not** distribution-free guaranteed coverage. |
| Aggregate savings uncertainty / fractional savings uncertainty | Library | `uq/savings_uncertainty.py`, `uq/fsu.py`; covariance propagation, not a certification. |
| Bootstrap / moving-block bootstrap / Monte Carlo diagnostics | Integrated, purpose-specific | `tests_catalog/ci_method.py`, `monte_carlo.py`, individual test contracts. Not a general simulation platform. |
| Bayesian posterior inference, MCMC, variational inference, Bayesian model averaging | Not found | Bayesian Ridge and Gaussian process candidates exist, but do not establish a general MCMC/variational-inference/model-averaging workbench. |
| Financial VaR, CVaR / expected shortfall, drawdown, portfolio risk | Not found | Savings uncertainty and deviation alerts are not financial risk analytics. |
| PSI, KS, Wasserstein covariate drift | Library/CLI | `monitoring/drift.py`, `cli.py` reporting command. The primary `gui/page_monitor.py` uses `core/monitor.py`; do not conflate the two paths. |
| Residual Page-Hinkley, CUSUM, Pettitt, variance-ratio monitoring | Library/CLI | `monitoring/drift.py`; advice is revalidation, never automatic baseline mutation/refitting. |
| Jensen-Shannon/KL divergence, categorical chi-square drift, ADWIN, DDM, EDDM | Not found | Not established by the numeric drift implementation. |
| Demographic parity, equalized odds, disparate impact, subgroup fairness mitigation | Not found | Causal group covariate balance is not algorithmic fairness. |
| General adversarial robustness / stress-testing suite | Not found | Leakage canaries, validity-domain checks, plausibility and sensitivity diagnostics are narrower safeguards. |
| Chronological train/validation/holdout | Integrated | `validation/splitting.py`, `core/evaluation.py`. |
| Expanding, rolling, purged blocked walk-forward | Integrated | `validation/walkforward.py`, `purge.py`, `pipeline.py`, `core/evaluation.py`; embargo accounts for lag/rolling memory. |
| Nested temporal HPO | Integrated / reusable library | `core/models.py`, `core/evaluation.py`; standalone Optuna helper in `validation/nested_hpo.py`. |
| Arbitrary shuffled K-fold / stratified / group / repeated / leave-one-out / CPCV as main model CV | Not established | Main time-series evaluator explicitly forbids shuffled folds. Descriptive regime-classification CV in `core/mining.py` is not the main baseline validation contract. |

## 4. Feature selection and engineering

| Requested capability | Finding | Evidence and limits |
|---|---|---|
| Correlation, mutual information, Lasso feature selection | Integrated | `core/features.py::select_features`, `validation/preprocessing.py`; selection is constrained by sample-size rules and in-fold fitting. |
| VIF / collinearity guards, explicit include/exclude, integrity rules | Integrated | `core/vsg.py`, `core/integrity.py`; target and sibling-target leakage remain prohibited. |
| RFE / RFECV, Boruta, sequential forward/backward selection, genetic selection | Not found | Ranking or an AUTO heuristic does not implement these algorithms. |
| Multi-target feature consensus | Partial | `models/f8_regime.py` has Kendall-W feature consensus; this is not evidence of a general multi-output consensus selector. |
| Causal lag / rolling / calendar / cyclic / degree-day / Fourier features | Integrated, policy-dependent | `core/features.py`, `validation/preprocessing.py`; degree-day semantics require the appropriate explicit domain policy. |
| Polynomial / interaction / spline / GAM-like basis | Integrated / CLI family catalog | `core/models.py`, `models/f4_basis.py`; spline approximation is not every GAM backend. |
| Standard scaling | Integrated | In-fold preprocessing and model-specific designs. |
| General RobustScaler / MinMax / power transforms / learned categorical encoding / target encoding | Not found as a general feature pipeline | Not implied by sklearn being installed. |
| PCA / t-SNE / UMAP and clustering | Integrated descriptive mining, conditional | `core/mining.py`; optional backends may skip. These are not automatically authorized baseline predictors. |
| Feature hashing / text TF-IDF / language embeddings / image embeddings | Not found | Numeric dimensionality reduction is not NLP/CV embedding support. |

## 5. Baselines, classical models, ensembles, AutoML, neural models, forecasting

| Requested capability | Finding | Evidence and limits |
|---|---|---|
| Mean baseline | Integrated | `core/models.py::MeanEst`, `models/portfolio.py::MeanBaselineWrapper`. |
| Median / last-value / seasonal-naive / random-walk baseline catalog | Not found as dedicated main candidates | ETS or seasonal decomposition does not establish these separate benchmark models. |
| OLS, Ridge, Lasso, ElasticNet, Bayesian Ridge, quantile regression | Integrated | `core/models.py` candidate registry; linear-family modules under `models`. |
| Degree-day, change-point / piecewise, TOWT | Integrated when domain policy permits | `core/models.py`, F2 and portfolio wrappers; not globally enabled for neutral datasets. |
| SVR and Gaussian process regression | Integrated | `core/models.py` candidates; caps/settings can restrict execution. |
| Huber/RANSAC robust regression, kNN regression and arbitrary standalone decision-tree regression | Not found as dedicated main candidates | kNN classification in mining and trees inside ensembles are different capabilities. |
| Random Forest, ExtraTrees, Gradient Boosting, XGBoost, LightGBM, CatBoost | Integrated, backend-dependent | `core/models.py`; F5 catalog also supports tree/boosting variants. They are **not** unused merely because some execution is optional. |
| Convex / NNLS stacking and blending | Integrated / CLI catalog | `core/pipeline.py` inner-OOF ensemble path, `models/ensemble.py`, `f10_stacking.py`. Not general voting, bagging, AdaBoost or arbitrary meta-learning. |
| Automated candidate competition, HPO, ranking, shortlist | Integrated, narrower than general AutoML | `core/pipeline.py`, `core/models.py`, `core/evaluation.py`. |
| Auto-sklearn, TPOT, H2O, FLAML, AutoGluon, NAS | Not found | Optuna-backed tuning does not mean these AutoML engines are integrated. |
| MLP | Integrated | `core/nn.py::SkMLP`, `core/models.py`; sample-size gate applies. |
| LSTM, GRU, TCN, 1D-CNN, CNN-LSTM, autoencoder regressor, N-BEATS | Integrated, optional PyTorch and sample gates | `core/nn.py::TORCH_ARCHS`, `core/models.py::build_portfolio`; inspecting only F6 (MLP/LSTM) would incorrectly classify the others as absent. These are numeric sequence regressors, not image/language models. |
| TFT, PatchTST, Informer / general Transformer forecasting | Not implemented in main candidate registry | `core/models.py` explicitly records TFT and PatchTST/Informer as unavailable architectures. |
| ARIMAX, SARIMAX, ETS | Library/CLI F7 catalog | `models/f7_timeseries.py`, `models/__init__.py::evaluate_f1_f10_catalog`, `cli.py` family-catalog command. Do not assume the desktop main candidate competition runs F7. |
| Fourier / STL decomposition, Prophet | Library/CLI F9 catalog; Prophet optional | `models/f9_decomposition.py`; existence of the wrapper does not prove Prophet is installed or invoked by the desktop. |
| VAR / VECM, TBATS / BATS, Croston intermittent demand, hierarchical reconciliation | Not found | Not implied by statsmodels or decomposition capabilities. |
| DeepAR, N-HiTS, TiDE, foundation forecasting models | Not found | N-BEATS is implemented; do not extrapolate that to every deep forecasting architecture. |
| General supervised classification product | Partial, descriptive mining only | `core/mining.py` has logistic/RF/ExtraTrees/GB/SVM/kNN/NB/MLP and optional boosting regime classifiers. This does not provide arbitrary classification targets, classification lifecycle, or a fairness workbench in the main regression product. |

## 6. Other application families

| Requested capability | Finding | Evidence and limits |
|---|---|---|
| General anomaly detection | Partial / integrated numeric methods | See outlier methods above and descriptive `core/mining.py`; no comprehensive anomaly model zoo. |
| Survival / Cox / AFT / Kaplan-Meier / competing risks | Not found | No production survival workflow identified. |
| Graph analytics / GNN / node classification / link prediction | Not found | A pipeline dependency DAG is not graph machine learning. |
| NLP classification / NER / sentiment / translation / summarization / topic modeling / LLM or RAG | Not found | Numeric report narratives are not a general NLP subsystem. |
| Image classification / detection / segmentation / OCR / vision models | Not found | Plotting and a 1D numeric CNN are not computer vision. |
| Recommenders / collaborative filtering / matrix factorization / ranking | Not found | Model leaderboard ranking is not recommendation-system learning-to-rank. |
| Causal DiD, balance, propensity overlap and parallel-trend diagnostics | Integrated, constrained | T14 in `tests_catalog/t14_causal_group.py`; custom `causal/mv.py::run_causal_mv` is separately callable. Do not label every group comparison causal identification. |
| DoWhy identification / regression / refutation; EconML LinearDML | Library, optional, not primary GUI path | `causal/mv.py::optional_backend_effect`; no production caller outside the exported API was found. Implemented does not mean automatically used. |
| Causal forests / generalized CATE model zoo / IV / RD / synthetic control / causal discovery | Not found as complete workflows | The optional LinearDML adapter is not evidence for these separate algorithms. |
| HPO, numerical fitting, constrained ensemble optimization | Integrated, task-specific | Optuna tuning, regression fitting and simplex/NNLS weights exist. |
| General LP/MILP, scheduling, routing, multi-objective / Pareto / evolutionary optimization | Not found | Task-specific numerical minimization is not a general operations-research platform. |
| General discrete-event, agent-based, system-dynamics simulation / digital twins | Not found | Bootstrap and Monte Carlo test calibration are not these simulators. |
| Permutation importance, PDP, ICE | Integrated, optional calculation | `core/pipeline.py::_importance`, invoked for selected models when requested; custom PDP/ICE implementation, not necessarily sklearn's named helpers. |
| Tree SHAP | Integrated, optional | Same path; backend/errors recorded, not guaranteed for every model. |
| Analytical linear / coalition / permutation Shapley attribution | Library/CLI catalog support | `models/top5.py::compute_driver_attributions`; distinct from the desktop tree-SHAP path. |
| Diagnostic surrogate | Integrated / catalog support | `core/models.py`, `models/top5.py`; surrogate is labeled approximate, never the executable baseline. |
| LIME, ALE, counterfactual explanations, recourse, causal explanations | Not found | PDP/ICE or a surrogate does not establish these distinct explanation techniques. |

## How capabilities actually enter the application

1. `gui/pages_setup.py` creates DatasetSchema, VSG and TCP. Inference requires accepted, input-bound VSG and a preregistration-locked TCP (`core/authorization.py`).
2. Desktop analysis uses `core/pipeline.py::analyze_target`, including quality preprocessing, descriptive EDA/mining, feature preparation, `core/models.py` candidate evaluation and optional explanations. A candidate in a registry is not evidence that it ran for a particular dataset: inspect that run's evaluation register and rejection/skip reasons.
3. `models/__init__.py::evaluate_f1_f10_catalog` is separately invoked from `cli.py`. Its catalog is not synonymous with the desktop candidate list.
4. `cli.py` separately exposes UQ and reporting-monitoring paths. `gui/page_monitor.py` primarily calls `core/monitor.py`; the richer `monitoring/drift.py` API must not be claimed as automatically connected to every desktop monitoring action.
5. Optional causal adapters are exported library APIs, not proof of desktop invocation. Merely finding DoWhy/EconML/Prophet/torch in a dependency environment does not change reachability.

## VSG target-selection defect addressed in this branch

Previously, `rebuild_compliance_models()` ran on dataset load but target checklist and schema-role changes did not rebuild its fixed target tuple. The displayed target list and accepted authorization could consequently refer to an earlier selection.

The correction refreshes pending gates after checklist, schema-role/metadata, time-column, unit and schema-file edits; clears stale IDs, results and impact UI when selection becomes empty/invalid; retains the currently viewed target if still selected; and never carries acceptance into the rebuilt gates. Merely viewing another already-selected target or collecting unchanged setup does not rebuild the gates. The VSG dropdown remains a view over selected TARGET roles, not an unrestricted predictor-to-target switch.

Regression tests: `tests/gui/test_vsg_target_sync.py`. Test outcomes and any remaining verification limitations are recorded separately; this source audit itself is not runtime acceptance evidence.
