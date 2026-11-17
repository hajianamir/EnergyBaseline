"""Typed stock-flow dynamics, synchronous graph agents and frozen linear digital-twin validation."""

from __future__ import annotations

import numpy as np
from scipy.integrate import solve_ivp

from .common import chronological, digest, fields, integer, number, numeric, real_array


def stock_flow(spec):
    fields(spec, ("initial", "stoichiometry", "rate_matrix", "constant_rates", "duration", "steps"), ("nonnegative",))
    initial = real_array(spec["initial"], "initial")
    S = real_array(spec["stoichiometry"], "stoichiometry")
    K = real_array(spec["rate_matrix"], "rate_matrix")
    b = real_array(spec["constant_rates"], "constant_rates")
    if (
        initial.ndim != 1
        or not 1 <= len(initial) <= 30
        or S.ndim != 2
        or S.shape[0] != len(initial)
        or not 1 <= S.shape[1] <= 60
        or K.shape != (S.shape[1], len(initial))
        or b.shape != (S.shape[1],)
    ):
        raise ValueError("Stock/flow dimensions must agree explicitly.")
    duration = number(spec["duration"], "duration", 1e-6, 1e6)
    steps = integer(spec["steps"], "steps", 2, 1000)
    nonnegative = spec.get("nonnegative", True)
    if type(nonnegative) is not bool or nonnegative and (initial < 0).any():
        raise ValueError("Invalid nonnegativity policy/initial stocks.")
    if np.max(np.abs(S @ K)) * duration > 1000:
        raise ValueError("Dynamics stiffness/growth budget exceeded; rescale the model/time units.")
    # Fixed linear rates, not caller expressions. No silent clipping of negative stock values.
    A = S @ K
    drift = S @ b
    times = np.linspace(0, duration, steps + 1)
    fit = solve_ivp(
        lambda _, state: A @ state + drift, (0, duration), initial, t_eval=times, method="DOP853", rtol=1e-8, atol=1e-10
    )
    if not fit.success or not np.isfinite(fit.y).all() or fit.y.shape != (len(initial), steps + 1):
        raise ValueError("Stock-flow integration failed.")
    if nonnegative and fit.y.min() < -1e-7:
        raise ValueError("Declared nonnegative stocks crossed zero; no silent clipping/repair.")
    conserved = np.all(np.abs(S.sum(axis=0)) < 1e-12)
    drift_total = float(np.max(np.abs(fit.y.sum(axis=0) - initial.sum())))
    if conserved and drift_total > 1e-6 * max(1, float(np.abs(initial).sum())):
        raise ValueError("Conservation check failed.")
    return {
        "method": "DOP853 integration of declared linear stock-flow rates",
        "time_unit": "Caller-declared consistent model time unit",
        "steps": steps,
        "final_stocks": fit.y[:, -1].tolist(),
        "minimum_stocks": fit.y.min(axis=1).tolist(),
        "maximum_stocks": fit.y.max(axis=1).tolist(),
        "total_stock_conserved_by_structure": bool(conserved),
        "maximum_total_stock_deviation": drift_total,
        "function_evaluations": fit.nfev,
        "baseline_changed": False,
        "calibrated_to_reality": False,
    }


def agent_sir(spec):
    fields(
        spec,
        ("agents", "edges", "initial_infected", "transmission_rate", "recovery_rate", "step_size", "steps", "seed"),
        ("interventions",),
    )
    n = integer(spec["agents"], "agents", 2, 500)
    steps = integer(spec["steps"], "steps", 1, 1000)
    edges = spec["edges"]
    infected = spec["initial_infected"]
    if (
        not isinstance(edges, list)
        or len(edges) > 10000
        or not isinstance(infected, list)
        or not infected
        or len(set(infected)) != len(infected)
    ):
        raise ValueError("Declare bounded unique initial infected agent indices and contact edges.")
    adjacency = np.zeros((n, n), dtype=np.int16)
    seen = set()
    for edge in edges:
        if not isinstance(edge, list) or len(edge) != 2:
            raise ValueError("Edges must be explicit pairs.")
        a, b = [integer(v, "agent position", 0, n - 1) for v in edge]
        key = tuple(sorted((a, b)))
        if a == b or key in seen:
            raise ValueError("Contact graph forbids loops and duplicate undirected edges.")
        seen.add(key)
        adjacency[a, b] = adjacency[b, a] = 1
    state = np.zeros(n, dtype=int)
    for position in infected:
        state[integer(position, "infected position", 0, n - 1)] = 1
    beta = number(spec["transmission_rate"], "transmission_rate", 0, 100)
    gamma = number(spec["recovery_rate"], "recovery_rate", 0, 100)
    dt = number(spec["step_size"], "step_size", 1e-6, 100)
    schedule = np.ones(steps)
    last = -1
    for intervention in spec.get("interventions", []):
        fields(intervention, ("step", "transmission_multiplier"))
        start = integer(intervention["step"], "step", 0, steps - 1)
        if start <= last:
            raise ValueError("Interventions must be ordered with unique start steps.")
        schedule[start:] = number(intervention["transmission_multiplier"], "transmission_multiplier", 0, 10)
        last = start
    rng = np.random.default_rng(integer(spec["seed"], "seed", 0, 2**32 - 1))
    trajectory = []

    def counts():
        return [int(np.sum(state == s)) for s in (0, 1, 2)]

    trajectory.append(counts())
    for step in range(steps):
        contacts = adjacency @ (state == 1).astype(np.int16)
        infection = 1 - np.exp(-beta * schedule[step] * contacts * dt)
        recovery = 1 - np.exp(-gamma * dt)
        # All decisions use the previous state: newly infected agents cannot recover/transmit in this step.
        draws = rng.random((2, n))
        following = state.copy()
        following[(state == 0) & (draws[0] < infection)] = 1
        following[(state == 1) & (draws[1] < recovery)] = 2
        state = following
        trajectory.append(counts())
    if any(sum(row) != n for row in trajectory):
        raise ValueError("Agent population conservation failed.")
    return {
        "model": "Synchronous individual SIR on an explicit undirected contact graph",
        "trajectory_counts_SIR": trajectory,
        "peak_infected": max(row[1] for row in trajectory),
        "population": n,
        "step_size": dt,
        "agent_states_exported": False,
        "calibrated_epidemiology_claim": False,
        "baseline_changed": False,
    }


def digital_twin(frame, spec):
    fields(spec, ("time", "states", "inputs", "train_rows", "ridge"), ("time_unit_seconds",))
    times = chronological(frame, spec["time"])
    delta = times.diff().dropna().dt.total_seconds().to_numpy()
    if len(delta) < 20 or not np.allclose(delta, delta[0], rtol=1e-10, atol=1e-9):
        raise ValueError("Twin validation needs regular observed timestamps and at least 21 rows.")
    names = spec["states"]
    inputs = spec["inputs"]
    if (
        not isinstance(names, list)
        or not isinstance(inputs, list)
        or not names
        or set(names) & set(inputs)
        or spec["time"] in names + inputs
        or len(names) > 10
        or len(inputs) > 10
    ):
        raise ValueError("Declare disjoint state/input columns with at most ten of each.")
    values = numeric(frame, names + inputs)
    split = integer(spec["train_rows"], "train_rows", 15, len(frame) - 1)
    if split > 10000:
        raise ValueError("Twin calibration is bounded to 10000 rows.")
    unit = number(spec.get("time_unit_seconds", 1), "time_unit_seconds", 1e-6, 1e9)
    dt = delta[0] / unit
    regularization = number(spec["ridge"], "ridge", 0, 1e6)
    states = values[:, : len(names)]
    control = values[:, len(names) :]
    # Identify the discrete state transition directly, avoiding a derivative approximation.
    design = np.column_stack([states[: split - 1], control[: split - 1], np.ones(split - 1)])
    if np.linalg.matrix_rank(design) < design.shape[1] and regularization == 0:
        raise ValueError("Unregularized state-space calibration is rank deficient.")
    penalty = np.eye(design.shape[1]) * regularization
    penalty[-1, -1] = 0
    coefficients = np.linalg.solve(design.T @ design + penalty, design.T @ states[1:split])
    count = len(names)
    A = coefficients[:count].T
    B = coefficients[count:-1].T
    bias = coefficients[-1]
    predicted = []
    current = states[split - 1].copy()
    for step in range(split - 1, len(states) - 1):
        # Inputs are explicit externally prescribed controls, not observed future state feedback.
        current = A @ current + B @ control[step] + bias
        predicted.append(current.copy())
    predicted = np.asarray(predicted)
    if not np.isfinite(predicted).all():
        raise ValueError("Unstable/nonfinite frozen twin rollout.")
    error = predicted - states[split:]
    return {
        "model": "Frozen discrete affine state-space digital-twin candidate",
        "transition": A.tolist(),
        "control_matrix": B.tolist(),
        "bias": bias.tolist(),
        "step_in_model_time_units": float(dt),
        "spectral_radius": float(max(np.abs(np.linalg.eigvals(A)))),
        "train_rows": split,
        "test_rows": len(states) - split,
        "rmse_by_state": np.sqrt(np.mean(error**2, axis=0)).tolist(),
        "mae_by_state": np.mean(np.abs(error), axis=0).tolist(),
        "training_sha256": digest(values[:split].tolist()),
        "rollout": "Fixed calibration; externally prescribed control inputs, no observed holdout state feedback",
        "physical_validity_certified": False,
        "automatic_online_update": False,
        "baseline_changed": False,
    }
