"""Jinja2 template for the professional English report (interim HTML/CSS template for Qt rich-text fallback; ReportLab lands in Phase 8)."""

REPORT_TEMPLATE = r"""<html><head><meta charset="utf-8"><title>Energy Baseline Report</title>
<style>
 body{font-family:Arial,Helvetica,sans-serif;font-size:10pt;color:#1b2430;margin:18px}
 h1{color:#0b4f6c;font-size:22pt;margin-bottom:2px} h2{color:#0b4f6c;border-bottom:2px solid #0b4f6c;padding-bottom:2px;margin-top:26px;font-size:15pt}
 h3{color:#146c94;font-size:12pt;margin-top:16px} h4{color:#333;font-size:10.5pt;margin-bottom:2px}
 table{border-collapse:collapse;width:100%;margin:6px 0 10px 0;font-size:8.5pt} th{background:#0b4f6c;color:#fff;padding:3px 5px;text-align:left}
 td{border:1px solid #c9d3dc;padding:2px 5px;vertical-align:top} tr.alt td{background:#f2f6f9}
 pre{background:#f2f6f9;border:1px solid #c9d3dc;padding:6px;white-space:pre-wrap;font-size:8.5pt;font-family:Consolas,monospace}
 .warn{background:#fff3cd;border:1px solid #e0b100;padding:6px;margin:6px 0} .ok{color:#1a7f37} .bad{color:#b42318} .muted{color:#667085}
 .kpi{background:#e8f1f6;padding:6px 10px;margin:4px 0}
</style></head><body>
<h1>Energy Baseline Analytics Report</h1>
<p class="muted">Generated {{ created }} &middot; EnergyBaseline v{{ versions.app }} &middot; run #{{ run_id }} &middot; fully offline, local-first analysis</p>

<h2>1. Executive Summary</h2>
<p>{{ n_targets }} target(s) were analysed independently (separate preprocessing, feature selection, model portfolio, Top-5 shortlist and baseline selection per target).
Dataset: <b>{{ dataset.name or 'n/a' }}</b> ({{ dataset.rows or '?' }} rows, {{ dataset.cols or '?' }} columns). Random seed: {{ seed }}.</p>
<table><tr><th>Target</th><th>Status</th><th>Granularity</th><th>Observations</th><th>Best model (score)</th><th>CV(RMSE) %</th><th>NMBE %</th><th>R²</th><th>User-selected baseline</th></tr>
{% for t in targets %}<tr class="{{ loop.cycle('','alt') }}"><td>{{ t.name }}</td><td>{{ t.status }}</td><td>{{ t.gran }}</td><td>{{ t.n_obs }}</td>
<td>{{ t.best_name }} {% if t.best_score is not none %}({{ '%.1f'|format(t.best_score) }}){% endif %}</td><td>{{ t.cvrmse }}</td><td>{{ t.nmbe }}</td><td>{{ t.r2 }}</td><td>{{ t.selected or 'not yet selected' }}</td></tr>{% endfor %}</table>
{% for t in targets %}{% if t.notice %}<div class="warn"><b>{{ t.name }}:</b> {{ t.notice }}</div>{% endif %}{% if t.error %}<div class="warn"><b>{{ t.name }} FAILED:</b> {{ t.error }}</div>{% endif %}{% endfor %}

<h2>2. Multi-Target Comparative Summary</h2>
<table><tr><th>Target</th><th>Best model</th><th>Family</th><th>Key drivers (permutation importance)</th><th>Qualified models</th><th>Models evaluated</th><th>Quality score</th></tr>
{% for t in targets %}<tr class="{{ loop.cycle('','alt') }}"><td>{{ t.name }}</td><td>{{ t.best_name }}</td><td>{{ t.best_family }}</td><td>{{ t.drivers }}</td><td>{{ t.n_qualified }}</td><td>{{ t.n_models }}</td><td>{{ t.quality_score }}</td></tr>{% endfor %}</table>
{% if charts.comparison %}<img src="{{ charts.comparison }}" width="640">{% endif %}

<h2>3. Temporal Configuration (per target)</h2>
{% for t in targets if t.temporal %}<h4>{{ t.name }}</h4>
<table><tr><th>Declared</th><th>Detected</th><th>Applied</th><th>Regularity</th><th>Rows in &rarr; out</th><th>Duplicates</th><th>Gap intervals</th><th>Timezone / DST</th><th>Baseline window</th><th>Reporting window</th></tr>
<tr><td>{{ t.temporal.declared }}</td><td>{{ t.temporal.detected }}</td><td>{{ t.temporal.applied }}</td><td>{{ '%.0f'|format(100*t.temporal.regularity) }} %</td><td>{{ t.temporal.n_input_rows }} &rarr; {{ t.temporal.n_output_rows }}</td><td>{{ t.temporal.n_duplicates }}</td><td>{{ t.temporal.n_gaps_filled_as_missing }}</td><td>{{ t.temporal.timezone }} / {{ t.temporal.dst }}</td><td>{{ t.temporal.baseline_window[0] }} &ndash; {{ t.temporal.baseline_window[1] }}</td><td>{{ t.temporal.reporting_window or 'none' }}</td></tr></table>
<p>Aggregation applied: {{ t.agg_text }}</p>{% for m in t.temporal.messages %}<div class="warn">{{ m }}</div>{% endfor %}{% endfor %}

<h2>4. Input Data Description</h2>
<p>{{ dataset.description }}</p>
{% if data_dictionary %}<table><tr>{% for h in data_dictionary[0].keys() %}<th>{{ h }}</th>{% endfor %}</tr>{% for r in data_dictionary %}<tr class="{{ loop.cycle('','alt') }}">{% for v in r.values() %}<td>{{ v }}</td>{% endfor %}</tr>{% endfor %}</table>{% endif %}

<h2>5. Data Quality</h2>
{% for t in targets if t.status=='ok' %}<h4>{{ t.name }}</h4>
<p class="kpi">Quality score <b>{{ t.quality.score }}/100</b> &middot; completeness {{ t.quality.completeness }} &middot; validity {{ t.quality.validity }} &middot; uniqueness {{ t.quality.uniqueness }} &middot; regularity {{ t.quality.regularity }}</p>
<p>Outlier detection: <b>{{ t.outliers.method }}</b> (threshold {{ t.outliers.threshold }}), treatment <b>{{ t.outliers.treatment }}</b>; {{ t.outliers.target_flagged }} target observations flagged ({{ '%.2f'|format(100*t.outliers.target_share) }} %).</p>
<ul>{% for n in t.notes %}<li>{{ n }}</li>{% endfor %}</ul>{% endfor %}

<h2>6. Exclusions &amp; Reasons</h2>
<p>Nothing is deleted silently: every exclusion, transformation and imputation is listed (raw data remain untouched under <code>data/uploads/</code>).</p>
{% for t in targets if t.status=='ok' %}<h4>{{ t.name }}</h4>
<table><tr><th>Item</th><th>Kind</th><th>Reason</th><th>Metric</th></tr>{% for e in t.exclusions %}<tr class="{{ loop.cycle('','alt') }}"><td>{{ e.item }}</td><td>{{ e.kind }}</td><td>{{ e.reason }}</td><td>{{ e.metric }}</td></tr>{% endfor %}</table>{% endfor %}

<h2>7. Exploratory &amp; Engineering Analysis</h2>
{% for t in targets if t.status=='ok' %}<h3>{{ t.name }}</h3>
{% if t.charts.series %}<img src="{{ t.charts.series }}" width="640">{% endif %}{% if t.charts.profile %}<img src="{{ t.charts.profile }}" width="640">{% endif %}
<p>Distribution: skew {{ '%.2f'|format(t.eda.distribution.skew) }}, kurtosis {{ '%.2f'|format(t.eda.distribution.kurtosis) }}, CV {{ '%.1f'|format(t.eda.distribution.cv_percent or 0) }} %, load factor {{ '%.2f'|format(t.eda.distribution.load_factor or 0) }}.
{% if t.eda.trend %}Trend: {{ '%+.2f'|format(t.eda.trend.slope_pct_of_mean_per_year or 0) }} % of mean per year (Kendall &tau;={{ '%.2f'|format(t.eda.trend.kendall_tau) }}, p={{ '%.3g'|format(t.eda.trend.kendall_p) }}).{% endif %}</p>
{% if t.eda.degree_days %}<p>Degree-day analysis: temperature column <i>{{ t.eda.degree_days.temperature_column }}</i>, heating balance {{ '%.1f'|format(t.eda.degree_days.heating_balance) }}, cooling balance {{ '%.1f'|format(t.eda.degree_days.cooling_balance) }} (R²={{ '%.3f'|format(t.eda.degree_days.r2) }}).</p>{% endif %}
<p>Change points ({{ t.eda.change_points.method }}): {{ t.eda.change_points.timestamps or 'none detected' }}</p>
<ul>{% for p in t.eda.plausibility %}<li>{{ p }}</li>{% endfor %}</ul>
<h4>Relationship ranking</h4>{{ t.rel_html|safe }}{% endfor %}

<h2>8. Data Mining</h2>
{% for t in targets if t.status=='ok' %}<h3>{{ t.name }}</h3>
<table><tr><th>Method</th><th>Parameters</th><th>Results</th><th>Interpretation</th><th>Downstream influence</th></tr>
{% for m in t.mining %}<tr class="{{ loop.cycle('','alt') }}"><td>{{ m.method }} {% if m.status!='ok' %}<span class="muted">({{ m.status }})</span>{% endif %}</td><td>{{ m.params }}</td><td>{{ m.results }}</td><td>{{ m.interpretation }}</td><td>{{ m.downstream }}</td></tr>{% endfor %}</table>{% endfor %}

<h2>9. Feature Engineering &amp; Selection</h2>
{% for t in targets if t.status=='ok' %}<h4>{{ t.name }}</h4><p>{{ t.feature_info.n_candidate_features }} candidate features &rarr; {{ t.selected_features|length }} retained: {{ t.selected_features|join(', ') }}</p>
<p>Required input columns for monitoring: {{ t.required_columns|join(', ') }}</p>{% endfor %}

<h2>10. Model Portfolio &amp; Validation Design</h2>
<p>Families evaluated: linear/statistical (mean, degree-day, MLR, Ridge, Lasso, Elastic Net, polynomial, piecewise/change-point, TOWT, GAM/spline, Bayesian ridge, quantile), tree-based (RF, ExtraTrees, GB, XGBoost, LightGBM, CatBoost where installed),
kernel/probabilistic (SVR, Gaussian process) and neural networks (MLP, LSTM, GRU, TCN, 1D-CNN, CNN-LSTM, autoencoder regressor, N-BEATS; NN+tree blend only if justified).
All models are compared on identical, time-aware, purged validation folds; hyper-parameters are tuned inside each training fold (nested), never on validation data.</p>
{% for t in targets if t.status=='ok' %}<p><b>{{ t.name }}:</b> {{ t.validation_text }}</p>{% endfor %}

<h2>11. Model Leaderboards</h2>
{% for t in targets if t.status=='ok' %}<h3>{{ t.name }}</h3>{{ t.leaderboard_html|safe }}
<p class="muted">Scoring profile: {{ t.scoring.profile }}. Weights: {{ t.weights_text }}. Acceptance thresholds: CV(RMSE) &le; {{ t.thresholds.max_cvrmse }} %, |NMBE| &le; {{ t.thresholds.max_nmbe }} %, R² &ge; {{ t.thresholds.min_r2 }}.</p>{% endfor %}

<h2>12. Top-5 Shortlist with Explicit Formulas</h2>
{% for t in targets if t.status=='ok' %}<h3>{{ t.name }}</h3>{% if t.notice %}<div class="warn">{{ t.notice }}</div>{% endif %}
{% for s in t.shortlist %}<h4>#{{ s.rank }} &ndash; {{ s.name }} <span class="muted">({{ s.family }}, score {{ '%.1f'|format(s.score) }})</span></h4>
<p>R²={{ '%.3f'|format(s.metrics.r2) }} &middot; CV(RMSE)={{ '%.2f'|format(s.metrics.cvrmse) }} % &middot; NMBE={{ '%+.2f'|format(s.metrics.nmbe) }} % &middot; MAE={{ '%.4g'|format(s.metrics.mae) }} &middot; MAPE={{ '%.2f'|format(s.metrics.mape) }} % &middot; sMAPE={{ '%.2f'|format(s.metrics.smape) }} %</p>
<p>95 % interval: [{{ '%.4g'|format(s.uncertainty.lower) }}, {{ '%+.4g'|format(s.uncertainty.upper) }}] around the prediction ({{ s.uncertainty.basis }}). {{ s.reason }}</p>
<pre>{{ s.formula.text }}</pre>{% endfor %}{% endfor %}

<h2>13. Decision Rationale (winner vs runner-up)</h2>
{% for t in targets if t.status=='ok' %}<h3>{{ t.name }}</h3>{{ t.rationale_html|safe }}{% endfor %}

<h2>14. Selected Baseline</h2>
{% for t in targets if t.status=='ok' %}<h3>{{ t.name }}: {{ t.selected or ('(no selection yet - rank 1 shown for reference) ' ~ t.best_name) }}</h3>
{% if t.sel %}<pre>{{ t.sel.formula.text }}</pre>
{% if t.sel_terms %}<h4>Coefficients with physical interpretation</h4><table><tr><th>Term</th><th>Coefficient</th><th>Interpretation</th></tr>{% for r in t.sel_terms %}<tr class="{{ loop.cycle('','alt') }}"><td>{{ r.term }}</td><td>{{ r.coef }}</td><td>{{ r.text }}</td></tr>{% endfor %}</table>{% endif %}
<p>Prediction interval (95 %): prediction {{ '%.4g'|format(t.sel.uncertainty.lower) }} to {{ '%+.4g'|format(t.sel.uncertainty.upper) }}.</p>
{% if t.charts.fit %}<img src="{{ t.charts.fit }}" width="640">{% endif %}{% if t.charts.resid %}<img src="{{ t.charts.resid }}" width="640">{% endif %}{% if t.charts.importance %}<img src="{{ t.charts.importance }}" width="640">{% endif %}{% endif %}{% endfor %}

<h2>15. Comparative Diagnostics (Top-3)</h2>
{% for t in targets if t.status=='ok' %}<h3>{{ t.name }}</h3>{{ t.comparative_html|safe }}
<p class="muted">Durbin-Watson ~2 and p-values &gt; 0.05 (Jarque-Bera, Ljung-Box, Breusch-Pagan) indicate well-behaved residuals. Computed on out-of-fold residuals.</p>{% endfor %}

<h2>16. Uncertainty</h2>
<p>Uncertainty is expressed as the empirical 2.5 % / 97.5 % quantiles of out-of-fold residuals (distribution-free, valid for every model family). Fold-to-fold CV(RMSE) spread quantifies robustness.</p>
{% for t in targets if t.status=='ok' %}<h4>{{ t.name }}</h4><table><tr><th>Model</th><th>Lower</th><th>Upper</th><th>Fold CV(RMSE) %</th></tr>{% for s in t.shortlist %}<tr class="{{ loop.cycle('','alt') }}"><td>{{ s.name }}</td><td>{{ '%.4g'|format(s.uncertainty.lower) }}</td><td>{{ '%+.4g'|format(s.uncertainty.upper) }}</td><td>{{ s.fold_cvrmse|map('round',1)|join(' / ') }}</td></tr>{% endfor %}</table>{% endfor %}

<h2>17. Rejected Models Register</h2>
{% for t in targets if t.status=='ok' %}<h3>{{ t.name }}</h3><table><tr><th>Model</th><th>Family</th><th>Status</th><th>Reason</th></tr>{% for r in t.register %}<tr class="{{ loop.cycle('','alt') }}"><td>{{ r.model }}</td><td>{{ r.family }}</td><td>{{ r.status }}</td><td>{{ r.reason }}</td></tr>{% endfor %}</table>{% endfor %}

<h2>18. AUTO Resolutions Log</h2>
<p>Every AUTO decision with the data signals and rule that drove it. AUTO is deterministic: same data and seed &rarr; same decisions.</p>
<table><tr><th>Target</th><th>Setting</th><th>Mode</th><th>Value</th><th>Signals</th><th>Rule</th></tr>{% for a in auto %}<tr class="{{ loop.cycle('','alt') }}"><td>{{ a.target or 'global' }}</td><td>{{ a.setting }}</td><td>{{ a.mode }}</td><td>{{ a.value }}</td><td>{{ a.signals }}</td><td>{{ a.rule }}</td></tr>{% endfor %}</table>

<h2>19. User Selections Log</h2>
<table><tr><th>Time</th><th>Target</th><th>Model</th><th>Note</th><th>Active</th></tr>{% for s in selections %}<tr class="{{ loop.cycle('','alt') }}"><td>{{ s.at }}</td><td>{{ s.target }}</td><td>{{ s.model }}</td><td>{{ s.note }}</td><td>{{ 'yes' if s.active else 'history' }}</td></tr>{% else %}<tr><td colspan="5">No baseline has been selected yet.</td></tr>{% endfor %}</table>

<h2>20. Limitations &amp; Assumptions</h2>
<ul>
<li>Metrics are computed on out-of-fold data; no degrees-of-freedom correction is applied to CV(RMSE)/NMBE (ASHRAE 14 formulas use n&minus;p on in-sample fits). Thresholds are used as acceptance guides, not certification.</li>
<li>Weekly-granularity thresholds (20 % / 7 %) are an interpolation between ASHRAE 14 hourly and monthly limits (assumption).</li>
<li>Uncertainty intervals assume out-of-fold residuals are representative of future residuals (stationary error distribution).</li>
<li>Purged blocked K-fold uses data from both sides of a validation block; this suits baseline estimation but is not a forecasting evaluation.</li>
<li>Sequence NNs build windows within each array; at block seams the first rows use edge padding.</li>
<li>Categorical predictors with &gt;12 levels and other TARGET columns are never used as predictors. Optional packages (CatBoost, Prophet, UMAP, SHAP, mlxtend) are used when installed and reported as skipped otherwise.</li>
{% for t in targets if t.status=='ok' %}{% for n in t.limitations %}<li><b>{{ t.name }}:</b> {{ n }}</li>{% endfor %}{% endfor %}
</ul>

<h2>21. Local Storage Confirmation</h2>
<p>All data stayed on this computer. Database: <code>{{ storage.db_url }}</code> (SQLite fallback used: {{ storage.fallback }}). Folders: data <code>{{ storage.data }}</code>, models <code>{{ storage.models }}</code>, reports <code>{{ storage.reports }}</code>, logs <code>{{ storage.logs }}</code>. No data was transmitted to any service.</p>

<h2>22. Reproducibility</h2>
<p>Seed: <b>{{ seed }}</b>. Compute device for neural networks: {{ versions.nn_device }}.</p>
<table><tr><th>Component</th><th>Version</th></tr>{% for k,v in versions.items() %}<tr class="{{ loop.cycle('','alt') }}"><td>{{ k }}</td><td>{{ v }}</td></tr>{% endfor %}</table>
<h4>Configuration snapshot</h4><pre>{{ config_json }}</pre>

<h2>Appendix A. Audit Log</h2>
<table><tr><th>Time</th><th>Level</th><th>Category</th><th>Message</th></tr>{% for e in audit %}<tr class="{{ loop.cycle('','alt') }}"><td>{{ e.ts }}</td><td>{{ e.level }}</td><td>{{ e.category }}</td><td>{{ e.message }}</td></tr>{% endfor %}</table>
</body></html>"""
