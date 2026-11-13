"""Data-mining layer. Each method records: method, parameters, quantitative results, interpretation, downstream influence.
Every method is isolated: a failure (or missing optional package) is logged and reported, never fatal."""
from __future__ import annotations

import logging
import time
import warnings

import numpy as np
import pandas as pd

from .temporal import Granularity

log = logging.getLogger("energybaseline.mining")
warnings.filterwarnings("ignore")


def _rec(method, params, results, interp, downstream, status="ok"):
    return {"method": method, "params": params, "results": results, "interpretation": interp, "downstream": downstream, "status": status}


def _guard(name, fn, out):
    t0 = time.time()
    try:
        r = fn()
        if r:
            out.extend(r if isinstance(r, list) else [r])
    except ImportError as exc:
        out.append(_rec(name, {}, {}, f"Skipped: optional package missing ({exc.name}).", "none", "skipped"))
    except Exception as exc:
        log.warning("mining.%s failed: %s", name, exc)
        out.append(_rec(name, {}, {}, f"Failed: {exc}", "none", "failed"))
    log.info("mining.%s %.1fs", name, time.time() - t0)


def run_mining(df: pd.DataFrame, target: str, predictors: list[str], g: Granularity, seed: int, cancel=lambda: False) -> list[dict]:
    out: list[dict] = []
    cols = [target] + predictors
    d = df[cols].dropna()
    if len(d) < 30 or len(cols) < 2:
        return [_rec("all", {}, {}, "Too few complete observations (<30) or variables (<2) for data mining.", "none", "skipped")]
    if len(d) > 4000:
        d = d.iloc[:: int(np.ceil(len(d) / 4000))]
    Z = ((d - d.mean()) / d.std(ddof=0).replace(0, 1)).values
    Zp = Z[:, 1:] if Z.shape[1] > 1 else Z

    def pca():
        from sklearn.decomposition import PCA
        p = PCA(random_state=seed).fit(Z)
        cum = np.cumsum(p.explained_variance_ratio_)
        k90 = int(np.searchsorted(cum, 0.9) + 1)
        return _rec("PCA", {"components": Z.shape[1]}, {"explained_variance_ratio": np.round(p.explained_variance_ratio_[:6], 4).tolist(), "components_for_90pct": k90},
                    f"{k90} of {Z.shape[1]} components explain 90 % of the variance; strong redundancy suggests collinear drivers." if k90 < Z.shape[1] else "Variance is spread over all components (low redundancy).",
                    "Informs the collinearity guard (VIF/correlation pruning is applied independently).")

    def kmeans():
        from sklearn.cluster import KMeans
        from sklearn.metrics import silhouette_score
        best = (-1, 2, None)
        for k in range(2, 7):
            km = KMeans(k, n_init=5, random_state=seed).fit(Z)
            s = silhouette_score(Z, km.labels_, sample_size=min(len(Z), 1500), random_state=seed)
            if s > best[0]:
                best = (s, k, km)
        s, k, km = best
        prof = pd.DataFrame(km.cluster_centers_ * d.std(ddof=0).values + d.mean().values, columns=cols).round(3)
        lab = pd.Series(km.labels_)
        return _rec("K-Means (regime discovery)", {"k_range": "2-6", "selected_k": k}, {"silhouette": round(float(s), 3), "cluster_sizes": lab.value_counts().sort_index().tolist(), "centers_target_mean": prof[target].tolist()},
                    f"{k} operating regimes found (silhouette {s:.2f}); regime centres differ in mean {target} by {prof[target].max() - prof[target].min():.3g}.",
                    "Reported for engineering insight; regime labels are NOT used as model inputs (they are derived from the target = leakage).") , km.labels_

    labels_holder: dict = {}

    def kmeans_wrap():
        r, lab = kmeans()
        labels_holder["labels"] = lab
        return r

    def gmm():
        from sklearn.mixture import GaussianMixture
        bics = {k: GaussianMixture(k, random_state=seed).fit(Z).bic(Z) for k in range(1, 6)}
        k = min(bics, key=bics.get)
        return _rec("Gaussian mixture", {"k_range": "1-5"}, {"bic": {k_: round(v, 1) for k_, v in bics.items()}, "best_k": k}, f"BIC prefers {k} Gaussian component(s).", "Cross-check of K-Means regime count.")

    def dbscan():
        from sklearn.cluster import DBSCAN
        from sklearn.neighbors import NearestNeighbors
        kd = np.sort(NearestNeighbors(n_neighbors=5).fit(Z).kneighbors(Z)[0][:, -1])
        eps = float(np.quantile(kd, 0.9))
        lab = DBSCAN(eps=eps, min_samples=5).fit_predict(Z)
        return _rec("DBSCAN", {"eps": round(eps, 3), "min_samples": 5}, {"clusters": int(len(set(lab)) - (1 if -1 in lab else 0)), "noise_share": round(float((lab == -1).mean()), 4)},
                    f"{(lab == -1).mean():.1%} of points are density outliers.", "Compared with the anomaly consensus below.")

    def hdbscan():
        from sklearn.cluster import HDBSCAN
        lab = HDBSCAN(min_cluster_size=max(10, len(Z) // 50)).fit_predict(Z)
        return _rec("HDBSCAN", {"min_cluster_size": max(10, len(Z) // 50)}, {"clusters": int(len(set(lab)) - (1 if -1 in lab else 0)), "noise_share": round(float((lab == -1).mean()), 4)}, "Hierarchical density clusters.", "Informational.")

    def hier():
        from sklearn.cluster import AgglomerativeClustering
        from sklearn.metrics import silhouette_score
        sub = Z[:: max(1, len(Z) // 1500)]
        sc = {k: silhouette_score(sub, AgglomerativeClustering(k, linkage="ward").fit_predict(sub)) for k in range(2, 6)}
        k = max(sc, key=sc.get)
        return _rec("Hierarchical (Ward)", {"k_range": "2-5"}, {"silhouette": {k_: round(v, 3) for k_, v in sc.items()}, "best_k": k}, f"Ward linkage suggests {k} groups.", "Informational.")

    def kmedoids():  # simple PAM-like on a sample (no extra dependency)
        from sklearn.metrics import pairwise_distances
        sub = Z[:: max(1, len(Z) // 400)]
        D = pairwise_distances(sub)
        rng = np.random.default_rng(seed)
        k = 3
        med = rng.choice(len(sub), k, replace=False)
        for _ in range(20):
            lab = D[:, med].argmin(1)
            new = np.array([np.where(lab == j)[0][D[np.ix_(lab == j, lab == j)].sum(1).argmin()] if (lab == j).any() else med[j] for j in range(k)])
            if (new == med).all():
                break
            med = new
        return _rec("K-Medoids (PAM-style)", {"k": k, "sample": len(sub)}, {"cost": round(float(D[np.arange(len(sub)), med[D[:, med].argmin(1)]].sum()), 2)}, "Robust cluster exemplars are actual observations.", "Informational.")

    def kpca_tsne_umap():
        res = []
        from sklearn.decomposition import FactorAnalysis, FastICA, KernelPCA
        kp = KernelPCA(n_components=2, kernel="rbf").fit(Z[:: max(1, len(Z) // 800)])
        res.append(_rec("Kernel PCA", {"kernel": "rbf", "n_components": 2}, {"top_eigenvalues": np.round(kp.eigenvalues_[:2], 3).tolist()}, "Non-linear structure projection.", "Informational."))
        fa = FactorAnalysis(n_components=min(2, Z.shape[1] - 1), random_state=seed).fit(Z)
        res.append(_rec("Factor analysis", {"n_components": fa.n_components}, {"noise_variance": np.round(fa.noise_variance_, 3).tolist()}, "Latent common factors behind the measured variables.", "Informational."))
        ic = FastICA(n_components=min(3, Z.shape[1]), random_state=seed, max_iter=500).fit(Z)
        res.append(_rec("ICA (FastICA)", {"n_components": ic.n_components}, {"iterations": int(ic.n_iter_)}, "Statistically independent source signals.", "Informational."))
        from sklearn.manifold import TSNE
        sub = Z[:: max(1, len(Z) // 800)]
        ts = TSNE(n_components=2, perplexity=min(30, max(5, len(sub) // 4)), random_state=seed).fit(sub)
        res.append(_rec("t-SNE", {"perplexity": ts.perplexity, "sample": len(sub)}, {"kl_divergence": round(float(ts.kl_divergence_), 4)}, "2-D neighbourhood embedding (visual use only).", "Informational."))
        try:
            import umap
            um = umap.UMAP(random_state=seed).fit(sub)
            res.append(_rec("UMAP", {"n_neighbors": 15}, {"embedding_shape": list(um.embedding_.shape)}, "Manifold embedding.", "Informational."))
        except ImportError:
            res.append(_rec("UMAP", {}, {}, "Skipped: 'umap-learn' not installed.", "none", "skipped"))
        return res

    def som():  # compact self-organising map (numpy)
        rng = np.random.default_rng(seed)
        gx = gy = 5
        W = rng.normal(size=(gx * gy, Z.shape[1]))
        grid = np.array([(i, j) for i in range(gx) for j in range(gy)], float)
        sub = Z[:: max(1, len(Z) // 1500)]
        T = 15
        for t in range(T):
            lr, sig = 0.5 * (1 - t / T) + 0.01, 2.0 * (1 - t / T) + 0.5
            for x in sub[rng.permutation(len(sub))]:
                b = ((W - x) ** 2).sum(1).argmin()
                h = np.exp(-((grid - grid[b]) ** 2).sum(1) / (2 * sig ** 2))[:, None]
                W += lr * h * (x - W)
        qe = float(np.mean(np.sqrt(((sub[:, None, :] - W[None]) ** 2).sum(2)).min(1)))
        return _rec("Self-organising map", {"grid": "5x5", "epochs": T}, {"quantisation_error": round(qe, 4)}, "Topology-preserving map of operating states.", "Informational.")

    def anomalies():
        from sklearn.covariance import EllipticEnvelope
        from sklearn.ensemble import IsolationForest
        from sklearn.neighbors import LocalOutlierFactor
        from sklearn.svm import OneClassSVM
        from .quality import detect_outliers
        flags = {}
        flags["Isolation Forest"] = IsolationForest(random_state=seed, contamination=0.02).fit_predict(Z) == -1
        flags["LOF"] = LocalOutlierFactor(n_neighbors=20, contamination=0.02).fit_predict(Z) == -1
        flags["One-Class SVM"] = OneClassSVM(nu=0.02, gamma="scale").fit(Z[:: max(1, len(Z) // 1500)]).predict(Z) == -1
        try:
            flags["Elliptic Envelope"] = EllipticEnvelope(contamination=0.02, random_state=seed).fit_predict(Z) == -1
        except Exception:
            pass
        zdf = pd.DataFrame(Z, columns=cols)
        for m in ("mahalanobis", "hbos", "autoencoder"):
            flags[{"mahalanobis": "Mahalanobis", "hbos": "HBOS", "autoencoder": "Autoencoder (reconstruction error)"}[m]] = detect_outliers(m, zdf, 3.5, seed).iloc[:, 0].values
        mat = np.column_stack(list(flags.values()))
        votes = mat.sum(1)
        cons = float((votes >= max(2, len(flags) // 2 + 1)).mean())
        return _rec("Anomaly detection ensemble", {"detectors": list(flags)}, {"flag_rate_per_detector": {k: round(float(v.mean()), 4) for k, v in flags.items()}, "consensus_rate": round(cons, 4)},
                    f"{cons:.2%} of observations are flagged by a majority of {len(flags)} detectors (multivariate anomalies).",
                    "Used as a cross-check of the univariate outlier treatment; flagged rows are not removed automatically.")

    def association():
        try:
            from mlxtend.frequent_patterns import apriori, association_rules, fpgrowth
            have = True
        except ImportError:
            have = False
        bins = pd.DataFrame(index=d.index)
        for c in cols:
            q = pd.qcut(d[c], 3, labels=["low", "mid", "high"], duplicates="drop")
            for lv in q.cat.categories:
                bins[f"{c}={lv}"] = q == lv
        if have:
            res = []
            for name, fn in (("Apriori", apriori), ("FP-Growth", fpgrowth)):
                fi = fn(bins, min_support=0.1, use_colnames=True)
                rules = association_rules(fi, metric="lift", min_threshold=1.2) if len(fi) else pd.DataFrame()
                tr = rules[rules["consequents"].apply(lambda s: any(x.startswith(f"{target}=") for x in s))] if len(rules) else rules
                top = []
                if len(tr):
                    for _, r in tr.sort_values("lift", ascending=False).head(3).iterrows():
                        top.append(f"{sorted(r['antecedents'])} -> {sorted(r['consequents'])} (lift {r['lift']:.2f}, conf {r['confidence']:.2f})")
                res.append(_rec(name, {"min_support": 0.1, "discretisation": "terciles"}, {"frequent_itemsets": int(len(fi)), "rules_for_target": top}, "Co-occurrence rules between driver levels and target level.", "Informational (engineering interpretation)."))
            return res
        # dependency-free fallback: lift between each predictor-level and high target
        hi = bins[f"{target}=high"] if f"{target}=high" in bins else None
        top = []
        if hi is not None:
            for c in bins.columns:
                if c.startswith(f"{target}="):
                    continue
                sup = bins[c].mean()
                if sup > 0.1:
                    conf = (bins[c] & hi).sum() / max(bins[c].sum(), 1)
                    lift = conf / max(hi.mean(), 1e-9)
                    top.append((lift, f"{c} -> {target}=high (lift {lift:.2f}, conf {conf:.2f})"))
        top = [t for _, t in sorted(top, reverse=True)[:3]]
        return _rec("Association rules (built-in fallback)", {"note": "mlxtend not installed; single-antecedent lift"}, {"rules_for_target": top}, "Driver levels most associated with high target values.", "Informational.")

    def ts_mining():
        res = []
        y = df[target].dropna()
        from statsmodels.tsa.seasonal import STL
        per = g.season_steps if g.season_steps > 1 else None
        if per and len(y) >= 2 * per + 5 and per <= 400:
            st = STL(y.values, period=per, robust=True).fit()
            fs = max(0.0, 1 - np.var(st.resid) / np.var(st.seasonal + st.resid))
            ft = max(0.0, 1 - np.var(st.resid) / np.var(st.trend + st.resid))
            res.append(_rec("STL decomposition", {"period_steps": per}, {"seasonal_strength": round(float(fs), 3), "trend_strength": round(float(ft), 3)},
                            f"Seasonal strength {fs:.2f}, trend strength {ft:.2f} at period {per} steps.", "Seasonality evidence supports the Fourier/TOWT features chosen under AUTO."))
        v = y.values - y.values.mean()
        if len(v) > 32:
            sp = np.abs(np.fft.rfft(v * np.hanning(len(v)))) ** 2
            fr = np.fft.rfftfreq(len(v), d=1.0)
            top = np.argsort(sp[1:])[::-1][:3] + 1
            res.append(_rec("Spectral analysis (FFT periodogram)", {"window": "Hann"}, {"dominant_periods_steps": [round(float(1 / fr[i]), 1) for i in top], "power_share": [round(float(sp[i] / sp[1:].sum()), 3) for i in top]},
                            "Dominant cycles of the target in units of sampling steps.", "Cross-check for the periods used in Fourier terms."))
        from .eda import change_points
        for name in ("PELT / BinSeg change-points",):
            cps, how = change_points(y.values)
            res.append(_rec(name, {"engine": how}, {"n_change_points": len(cps), "timestamps": [str(y.index[min(i, len(y) - 1)]) for i in cps]},
                            "Structural shifts in the level of the target (retrofit, schedule or process changes?)." if cps else "No significant level shifts.",
                            "Candidate break dates; if present, consider restricting the baseline window."))
        if 40 < len(y) <= 3000:
            from statsmodels.tsa.statespace.sarimax import SARIMAX
            yy = y.iloc[-min(len(y), 500):]
            s = per if per and per <= 24 and len(yy) > 3 * per else 0
            m = SARIMAX(yy.values, order=(1, 0, 1), seasonal_order=(1, 0, 0, s) if s else (0, 0, 0, 0)).fit(disp=False, maxiter=50)
            res.append(_rec("SARIMA(1,0,1)(1,0,0)s", {"s": s, "n": len(yy)}, {"aic": round(float(m.aic), 1)}, "Univariate benchmark (AIC) for autoregressive structure.", "Informational: residual autocorrelation justifies Durbin–Watson / Ljung–Box scoring."))
        try:
            import prophet
            _ = prophet
            res.append(_rec("Prophet", {}, {}, "Installed; not run by default to keep analysis time bounded.", "none", "skipped"))
        except Exception:
            res.append(_rec("Prophet", {}, {}, "Skipped: optional package 'prophet' not installed.", "none", "skipped"))
        # DTW between weekday/weekend typical-day profiles
        if g.tier == "subdaily" and len(y) > 7 * g.steps_per_day:
            wk = y.groupby([y.index.day_of_week >= 5, y.index.hour]).mean()
            a, b = wk.loc[False].values, wk.loc[True].values
            D = np.full((len(a) + 1, len(b) + 1), np.inf)
            D[0, 0] = 0
            for i in range(1, len(a) + 1):
                for j in range(1, len(b) + 1):
                    D[i, j] = abs(a[i - 1] - b[j - 1]) + min(D[i - 1, j], D[i, j - 1], D[i - 1, j - 1])
            res.append(_rec("DTW load-profile motif comparison", {"profiles": "weekday vs weekend hourly mean"}, {"dtw_distance": round(float(D[-1, -1]), 3), "euclidean": round(float(np.linalg.norm(a - b)), 3)},
                            "Shape distance between typical weekday and weekend days.", "Supports the calendar/TOWT feature choice."))
        return res

    def regimes():
        lab = labels_holder.get("labels")
        if lab is None or len(set(lab)) < 2:
            return None
        from sklearn.ensemble import ExtraTreesClassifier, GradientBoostingClassifier, RandomForestClassifier
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import cross_val_score
        from sklearn.naive_bayes import GaussianNB
        from sklearn.neighbors import KNeighborsClassifier
        from sklearn.neural_network import MLPClassifier
        from sklearn.svm import SVC
        Xr = Zp
        models = {"Logistic Regression": LogisticRegression(max_iter=500), "Random Forest": RandomForestClassifier(80, random_state=seed, n_jobs=2),
                  "Extra Trees": ExtraTreesClassifier(80, random_state=seed, n_jobs=2), "Gradient Boosting": GradientBoostingClassifier(random_state=seed, n_estimators=60),
                  "SVM": SVC(), "kNN": KNeighborsClassifier(), "Naive Bayes": GaussianNB(), "NN classifier (MLP)": MLPClassifier((32, 16), max_iter=300, random_state=seed)}
        for nm, imp in (("XGBoost", "xgboost"), ("LightGBM", "lightgbm")):
            try:
                if nm == "XGBoost":
                    from xgboost import XGBClassifier
                    models[nm] = XGBClassifier(n_estimators=60, verbosity=0, random_state=seed, n_jobs=2)
                else:
                    from lightgbm import LGBMClassifier
                    models[nm] = LGBMClassifier(n_estimators=60, verbose=-1, random_state=seed, n_jobs=2)
            except Exception:
                pass
        enc = pd.factorize(lab)[0]
        scores = {}
        for nm, m in models.items():
            try:
                scores[nm] = round(float(cross_val_score(m, Xr, enc, cv=3).mean()), 3)
            except Exception:
                pass
        best = max(scores, key=scores.get)
        return _rec("Regime classification (from drivers only)", {"classifiers": list(scores), "cv": "3-fold"}, {"accuracy": scores, "best": best},
                    f"Operating regimes can be predicted from the drivers alone with {scores[best]:.0%} accuracy ({best}).",
                    "High accuracy indicates the drivers carry the regime information a baseline needs.")

    for name, fn in [("pca", pca), ("kmeans", kmeans_wrap), ("gmm", gmm), ("dbscan", dbscan), ("hdbscan", hdbscan), ("hier", hier), ("kmedoids", kmedoids),
                     ("projections", kpca_tsne_umap), ("som", som), ("anomaly", anomalies), ("association", association), ("timeseries", ts_mining), ("regimes", regimes)]:
        if cancel():
            break
        _guard(name, fn, out)
    return out
