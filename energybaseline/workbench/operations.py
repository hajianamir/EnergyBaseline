"""Bounded LP/MILP, Pareto filtering and deterministic FCFS discrete-event simulation."""

from __future__ import annotations

import heapq

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from .common import fields, integer, number, numeric, real_array


def optimize(spec):
    fields(
        spec,
        ("objective", "lower", "upper", "integrality"),
        ("matrix", "constraint_lower", "constraint_upper", "time_limit_seconds"),
    )
    c = real_array(spec["objective"], "objective")
    if c.ndim != 1 or not 1 <= len(c) <= 500 or not np.isfinite(c).all():
        raise ValueError("Objective requires 1..500 finite coefficients.")
    lower, upper = real_array(spec["lower"], "lower"), real_array(spec["upper"], "upper")
    integral = spec["integrality"]
    if (
        not isinstance(integral, list)
        or len(integral) != len(c)
        or any(type(v) is not int or v not in (0, 1) for v in integral)
    ):
        raise ValueError("Integrality must declare 0 (continuous) or 1 (integer) for every variable.")
    if (
        lower.shape != c.shape
        or upper.shape != c.shape
        or not np.isfinite([lower, upper]).all()
        or (lower > upper).any()
    ):
        raise ValueError("Finite explicit ordered lower/upper bounds are required for every variable.")
    a = real_array(spec.get("matrix", []), "matrix")
    constraints = None
    if a.size:
        lo = real_array(spec.get("constraint_lower", []), "constraint_lower")
        hi = real_array(spec.get("constraint_upper", []), "constraint_upper")
        if (
            a.ndim != 2
            or a.shape[1] != len(c)
            or a.shape[0] > 2000
            or not np.isfinite(a).all()
            or lo.shape != (len(a),)
            or hi.shape != lo.shape
            or not np.isfinite([lo, hi]).all()
            or (lo > hi).any()
        ):
            raise ValueError("Constraints require a finite bounded matrix and explicit ordered finite row bounds.")
        constraints = LinearConstraint(a, lo, hi)
    elif a.ndim != 1 or "constraint_lower" in spec or "constraint_upper" in spec:
        raise ValueError("Constraint bounds require a nonempty matrix.")
    limit = number(spec.get("time_limit_seconds", 10), "time_limit_seconds", 0.01, 60)
    result = milp(
        c,
        integrality=np.asarray(integral),
        bounds=Bounds(lower, upper),
        constraints=constraints,
        options={"time_limit": limit, "mip_rel_gap": 0.0},
    )
    statuses = {0: "optimal", 1: "limit_reached", 2: "infeasible", 3: "unbounded", 4: "solver_error"}
    output = {
        "status": statuses.get(result.status, "solver_error"),
        "sense": "minimize",
        "solution": None,
        "objective": None,
        "feasibility_checked": False,
        "baseline_changed": False,
    }
    if result.x is not None and np.isfinite(result.x).all():
        x = np.asarray(result.x)
        tol = 1e-7
        feasible = (x >= lower - tol).all() and (x <= upper + tol).all()
        feasible = (
            feasible and (np.abs(x[np.asarray(integral, bool)] - np.round(x[np.asarray(integral, bool)])) <= tol).all()
        )
        if constraints is not None:
            feasible = feasible and (a @ x >= lo - tol).all() and (a @ x <= hi + tol).all()
        output["feasibility_checked"] = True
        if not feasible:
            raise ValueError("Solver incumbent failed independent feasibility checks.")
        output.update(solution=x.tolist(), objective=float(c @ x))
    if result.status == 0 and output["solution"] is None:
        raise ValueError("Solver claimed optimality without a valid solution.")
    return output


def pareto(frame, spec):
    fields(spec, ("objectives", "directions"))
    values = numeric(frame, spec["objectives"])
    if (
        len(values) > 2000
        or len(spec["directions"]) != values.shape[1]
        or any(v not in ("min", "max") for v in spec["directions"])
    ):
        raise ValueError("Pareto filtering accepts at most 2000 rows and one min/max direction per objective.")
    oriented = values * np.array([1 if s == "min" else -1 for s in spec["directions"]])
    positions = [
        i
        for i, point in enumerate(oriented)
        if not np.any(np.all(oriented <= point, axis=1) & np.any(oriented < point, axis=1))
    ]
    return {"nondominated_positions": positions, "duplicate_objectives_retained": True, "rows": len(values)}


def queue_simulation(frame, spec):
    fields(spec, ("arrival", "service", "servers"))
    arrivals, services = numeric(frame, [spec["arrival"], spec["service"]]).T
    servers = integer(spec["servers"], "servers", 1, 1000)
    if not len(arrivals) or (arrivals < 0).any() or (np.diff(arrivals) < 0).any() or (services < 0).any():
        raise ValueError("Arrivals must be nonnegative and ordered; service durations must be nonnegative.")
    # Stable customer order for simultaneous arrivals. A heap is the server-release event calendar.
    calendar = [(0.0, i) for i in range(servers)]
    heapq.heapify(calendar)
    waits, ends = [], []
    for arrival, service in zip(arrivals, services):
        available, server = heapq.heappop(calendar)
        start = max(arrival, available)
        end = start + service
        waits.append(start - arrival)
        ends.append(end)
        heapq.heappush(calendar, (end, server))
    horizon = max(ends)
    return {
        "customers": len(arrivals),
        "servers": servers,
        "discipline": "FCFS, stable input order on ties",
        "observation_start": 0,
        "drain_time": float(horizon),
        "mean_wait": float(np.mean(waits)),
        "maximum_wait": float(max(waits)),
        "mean_sojourn": float(np.mean(waits + services)),
        "utilization": float(services.sum() / (servers * horizon)) if horizon else None,
        "stochastic_guarantee": False,
        "warmup_removed": False,
    }
