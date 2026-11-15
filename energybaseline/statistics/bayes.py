"""Explicit Bayesian families: conjugate posteriors, real Metropolis sampling, mean-field VI and evidence-weighted mixtures."""

from __future__ import annotations

import numpy as np
from scipy import optimize, special, stats

from ..workbench.common import binary
from .common import preregistered_primitive, fields, integer, interval, number, real_array, vector


def _normal_posterior(y, sigma, prior_mean, prior_sd):
    n = len(y)
    variance = 1 / (n / sigma**2 + 1 / prior_sd**2)
    mean = variance * (y.sum() / sigma**2 + prior_mean / prior_sd**2)
    sample_mean = float(y.mean())
    logdet = n * np.log(sigma**2) + np.log1p(n * prior_sd**2 / sigma**2)
    quadratic = np.sum((y - sample_mean) ** 2) / sigma**2 + (sample_mean - prior_mean) ** 2 / (
        sigma**2 / n + prior_sd**2
    )
    log_evidence = -0.5 * (n * np.log(2 * np.pi) + logdet + quadratic)
    if not np.isfinite([variance, mean, log_evidence]).all() or variance <= 0:
        raise ValueError("Normal posterior numerical range exceeded; rescale explicitly.")
    return mean, variance, log_evidence


@preregistered_primitive
def conjugate(frame, names, parameters, level):
    method = parameters["_method"]
    p = {k: v for k, v in parameters.items() if k != "_method"}
    if len(names) != 1:
        raise ValueError("Conjugate inference needs one observed column.")
    tail = (1 - level) / 2
    if method == "beta_binomial":
        fields(p, ("prior_alpha", "prior_beta", "threshold"))
        y = binary(frame, names[0])
        a = number(p["prior_alpha"], "prior_alpha", 1e-6, 1e6) + int(y.sum())
        b = number(p["prior_beta"], "prior_beta", 1e-6, 1e6) + len(y) - int(y.sum())
        threshold = number(p["threshold"], "threshold", 0, 1)
        low, high = stats.beta.ppf([tail, 1 - tail], a, b)
        return {
            "family": "beta-binomial",
            "n": len(y),
            "posterior_mean": float(a / (a + b)),
            "posterior_variance": float(a * b / ((a + b) ** 2 * (a + b + 1))),
            "posterior_probability_above_threshold": float(stats.beta.sf(threshold, a, b)),
            "credible_interval": interval(low, high, level, interpretation="Bayesian equal-tailed credible"),
            "prior_sensitivity_or_model_adequacy_proven": False,
        }
    fields(p, ("sigma", "prior_mean", "prior_sd", "threshold"))
    y = vector(frame, names).ravel()
    sigma = number(p["sigma"], "sigma", 1e-8, 1e8)
    mu0 = number(p["prior_mean"], "prior_mean")
    sd0 = number(p["prior_sd"], "prior_sd", 1e-8, 1e8)
    threshold = number(p["threshold"], "threshold")
    mean, variance, evidence = _normal_posterior(y, sigma, mu0, sd0)
    sd = np.sqrt(variance)
    low, high = stats.norm.ppf([tail, 1 - tail], loc=mean, scale=sd)
    return {
        "family": "known-noise normal mean with normal prior",
        "n": len(y),
        "posterior_mean": float(mean),
        "posterior_variance": float(variance),
        "log_marginal_likelihood": float(evidence),
        "posterior_probability_above_threshold": float(stats.norm.sf(threshold, loc=mean, scale=sd)),
        "credible_interval": interval(low, high, level, interpretation="Bayesian equal-tailed credible"),
        "noise_variance_estimated_from_data": False,
    }


@preregistered_primitive
def model_average(frame, names, parameters, level):
    fields(parameters, ("sigma", "models"))
    if len(names) != 1:
        raise ValueError("Normal-mean model averaging needs one observed column.")
    y = vector(frame, names).ravel()
    sigma = number(parameters["sigma"], "sigma", 1e-8, 1e8)
    models = parameters["models"]
    if not isinstance(models, list) or not 2 <= len(models) <= 20:
        raise ValueError("Declare two to twenty competing prior models.")
    means, variances, log_evidence, weights = [], [], [], []
    for model in models:
        fields(model, ("prior_mean", "prior_sd", "weight"))
        mean, variance, evidence = _normal_posterior(
            y, sigma, number(model["prior_mean"], "prior_mean"), number(model["prior_sd"], "prior_sd", 1e-8, 1e8)
        )
        means.append(mean)
        variances.append(variance)
        log_evidence.append(evidence)
        weights.append(number(model["weight"], "weight", 1e-12, 1))
    if not np.isclose(sum(weights), 1, atol=1e-10, rtol=0):
        raise ValueError("Prior model probabilities must sum to one.")
    weights = special.softmax(np.log(weights) + np.asarray(log_evidence))
    means, variances = np.asarray(means), np.asarray(variances)
    mean = float(weights @ means)
    variance = float(weights @ (variances + (means - mean) ** 2))
    tail = (1 - level) / 2
    lo, hi = float(np.min(means - 12 * np.sqrt(variances))), float(np.max(means + 12 * np.sqrt(variances)))

    def cdf(value):
        return float(weights @ stats.norm.cdf(value, loc=means, scale=np.sqrt(variances)))

    low = optimize.brentq(lambda value: cdf(value) - tail, lo, hi)
    high = optimize.brentq(lambda value: cdf(value) - (1 - tail), lo, hi)
    return {
        "family": "Bayesian normal-mean prior-model averaging",
        "posterior_model_probabilities": weights.tolist(),
        "posterior_mean": mean,
        "posterior_variance": max(0.0, variance),
        "credible_interval": interval(low, high, level, interpretation="Bayesian mixture equal-tailed credible"),
        "n": len(y),
        "log_evidence": [float(x) for x in log_evidence],
        "scope": "Evidence-weighted declared prior models with common known noise; not arbitrary estimator averaging.",
    }


def _linear(frame, names, p):
    if not 2 <= len(names) <= 10:
        raise ValueError("Linear Bayesian inference requires target plus one to nine real predictors.")
    values = vector(frame, names, 10)
    y = values[:, 0]
    X = np.column_stack([np.ones(len(y)), values[:, 1:]])
    sigma = number(p["sigma"], "sigma", 1e-6, 1e6)
    prior_mean = real_array(p["prior_mean"], "prior_mean")
    prior_sd = real_array(p["prior_sd"], "prior_sd")
    if prior_mean.shape != (X.shape[1],) or prior_sd.shape != prior_mean.shape or (prior_sd <= 0).any():
        raise ValueError("Declare prior mean/sd per coefficient, intercept first.")
    precision = X.T @ X / sigma**2 + np.diag(1 / prior_sd**2)
    center = np.linalg.solve(precision, X.T @ y / sigma**2 + prior_mean / prior_sd**2)
    if not np.isfinite(precision).all() or not np.isfinite(center).all():
        raise ValueError("Linear posterior numerical range exceeded.")
    return X, y, prior_mean, prior_sd, precision, center


def _diagnostics(samples):
    chains, draws, dimension = samples.shape
    half = draws // 2
    split = np.concatenate([samples[:, :half], samples[:, -half:]], axis=0)
    within = split.var(axis=1, ddof=1).mean(axis=0)
    between = half * split.mean(axis=1).var(axis=0, ddof=1)
    variance = (half - 1) / half * within + between / half
    rhat = np.sqrt(variance / within)
    # Geyer initial positive/monotone autocorrelation-pair sequence on each split chain.
    centered = split - split.mean(axis=1, keepdims=True)
    frequency = np.fft.rfft(centered, n=2 ** int(np.ceil(np.log2(2 * half))), axis=1)
    autocov = np.fft.irfft(frequency * frequency.conj(), axis=1)[:, :half].real / half
    rho = 1 - (within[None, :] - autocov.mean(axis=0)) / variance[None, :]
    rho[0] = 1
    ess = []
    for j in range(dimension):
        total, previous = 0.0, np.inf
        for lag in range(0, half - 1, 2):
            pair = rho[lag, j] + rho[lag + 1, j]
            if not np.isfinite(pair) or pair < 0:
                break
            pair = min(pair, previous)
            total += pair
            previous = pair
        tau = max(1.0, -1 + 2 * total)
        ess.append(float(min(chains * draws, split.shape[0] * half / tau)))
    return rhat, ess


@preregistered_primitive
def metropolis(frame, names, parameters, level):
    fields(parameters, ("sigma", "prior_mean", "prior_sd", "proposal_sd", "seed"), ("draws", "warmup", "chains"))
    X, y, mu0, sd0, precision, center = _linear(frame, names, parameters)
    draws = integer(parameters.get("draws", 2000), "draws", 500, 5000)
    warmup = integer(parameters.get("warmup", 1000), "warmup", 200, 5000)
    chains = integer(parameters.get("chains", 4), "chains", 4, 8)
    seed = integer(parameters["seed"], "seed", 0, 2**32 - 1)
    if chains * (draws + warmup) * X.size > 50_000_000:
        raise ValueError("MCMC plan resource bound exceeded.")
    proposal = real_array(parameters["proposal_sd"], "proposal_sd")
    if proposal.shape != mu0.shape or (proposal <= 0).any():
        raise ValueError("Proposal sd must be explicit and positive per coefficient.")
    samples, acceptance = np.empty((chains, draws, len(mu0))), []
    for chain in range(chains):
        rng = np.random.default_rng(np.random.SeedSequence([seed, chain]))
        current = mu0 + rng.normal(size=len(mu0)) * proposal * 2
        logp = -0.5 * (current - center) @ precision @ (current - center)
        accepted = 0
        for step in range(warmup + draws):
            candidate = current + rng.normal(size=len(mu0)) * proposal
            score = -0.5 * (candidate - center) @ precision @ (candidate - center)
            move = np.log(rng.uniform()) < min(0.0, score - logp)
            if move:
                current, logp = candidate, score
            if step >= warmup:
                samples[chain, step - warmup] = current
                accepted += int(move)
        acceptance.append(accepted / draws)
    rhat, ess = _diagnostics(samples)
    valid = bool(np.isfinite(rhat).all())
    converged = valid and bool((rhat < 1.05).all()) and min(ess) >= 100 and all(0.05 < a < 0.8 for a in acceptance)
    merged = samples.reshape(-1, samples.shape[-1])
    tail = (1 - level) / 2
    bounds = np.quantile(merged, [tail, 1 - tail], axis=0)
    return {
        "family": "Gaussian linear likelihood / independent normal coefficient priors",
        "algorithm": "fixed-kernel random-walk Metropolis; no sampling-phase adaptation",
        "status": "diagnostics_passed" if converged else "diagnostic_warning",
        "posterior_means": merged.mean(axis=0).tolist(),
        "credible_intervals": [
            interval(lo, hi, level, interpretation="Bayesian MCMC approximate credible") for lo, hi in bounds.T
        ],
        "split_rhat": rhat.tolist() if valid else None,
        "rhat_method": "classical split, not rank-normalized",
        "effective_sample_size": ess,
        "acceptance_by_chain": acceptance,
        "draws_per_chain": draws,
        "warmup_per_chain": warmup,
        "chains": chains,
        "seed": seed,
        "guarantee": "Diagnostics cannot prove convergence. Do not trust intervals when diagnostics warn.",
    }


@preregistered_primitive
def variational(frame, names, parameters, level):
    fields(parameters, ("sigma", "prior_mean", "prior_sd"), ("max_iterations",))
    X, y, mu0, sd0, precision, center = _linear(frame, names, parameters)
    dimension = len(mu0)
    maximum = integer(parameters.get("max_iterations", 1000), "max_iterations", 20, 2000)
    diagonal = np.diag(precision)

    def objective(z):
        mean, log_sd = z[:dimension], z[dimension:]
        var = np.exp(2 * log_sd)
        residual = mean - center
        value = 0.5 * (residual @ precision @ residual + diagonal @ var - 2 * log_sd.sum())
        gradient = np.r_[precision @ residual, diagonal * var - 1]
        return value, gradient

    initial = np.r_[mu0, np.clip(np.log(sd0), -20, 20)]
    fit = optimize.minimize(
        objective,
        initial,
        jac=True,
        method="L-BFGS-B",
        bounds=[(None, None)] * dimension + [(-20, 20)] * dimension,
        options={"maxiter": maximum, "ftol": 1e-12, "gtol": 1e-8},
    )
    mean, log_sd = fit.x[:dimension], fit.x[dimension:]
    sd = np.exp(log_sd)
    residual = mean - center
    kl = 0.5 * (
        diagonal @ sd**2
        + residual @ precision @ residual
        - dimension
        - np.linalg.slogdet(precision)[1]
        - 2 * log_sd.sum()
    )
    z = stats.norm.ppf((1 + level) / 2)
    active_bounds = bool((abs(log_sd) >= 20 - 1e-6).any())
    return {
        "family": "Gaussian linear likelihood / independent normal coefficient priors",
        "algorithm": "mean-field Gaussian reverse-KL variational optimization, L-BFGS-B",
        "status": "optimized" if fit.success and not active_bounds else "optimization_warning",
        "optimizer_success": bool(fit.success),
        "iterations": int(fit.nit),
        "active_log_sd_bounds": active_bounds,
        "variational_means": mean.tolist(),
        "variational_sd": sd.tolist(),
        "kl_to_exact_gaussian_posterior": float(max(0, kl)),
        "credible_intervals": [
            interval(m - z * s, m + z * s, level, interpretation="Bayesian mean-field approximate credible")
            for m, s in zip(mean, sd)
        ],
        "limitation": "Independent variational factors can understate correlated posterior uncertainty; these are not exact marginal intervals.",
    }
