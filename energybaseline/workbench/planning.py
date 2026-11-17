"""Explicit CVRP/job-shop MILP generators and constrained NSGA-II; independently checked incumbents."""

from __future__ import annotations

from itertools import combinations

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from .common import fields, integer, number, real_array


def _solve(c, lower, upper, integral, rows, lo, hi, limit):
    matrix = np.asarray(rows, dtype=float)
    fit = milp(
        np.asarray(c),
        integrality=np.asarray(integral),
        bounds=Bounds(lower, upper),
        constraints=LinearConstraint(matrix, lo, hi),
        options={"time_limit": limit, "mip_rel_gap": 0.0},
    )
    status = {0: "optimal", 1: "limit_reached", 2: "infeasible", 3: "unbounded", 4: "solver_error"}.get(
        fit.status, "solver_error"
    )
    if fit.x is None:
        if status == "optimal":
            raise ValueError("Solver claimed optimality without a feasible incumbent.")
        return status, None
    x = fit.x
    indices = np.asarray(integral, dtype=bool)
    if not (
        np.isfinite(x).all()
        and (x >= np.asarray(lower) - 1e-6).all()
        and (x <= np.asarray(upper) + 1e-6).all()
        and (np.abs(x[indices] - np.rint(x[indices])) < 1e-6).all()
        and (matrix @ x >= np.asarray(lo) - 1e-6).all()
        and (matrix @ x <= np.asarray(hi) + 1e-6).all()
    ):
        raise ValueError("MILP incumbent failed independent algebraic feasibility checks.")
    return status, x


def routing(spec):
    fields(spec, ("costs", "demands", "vehicle_capacity", "vehicles"), ("time_limit_seconds",))
    cost = real_array(spec["costs"], "costs")
    demand = real_array(spec["demands"], "demands")
    if (
        cost.ndim != 2
        or cost.shape[0] != cost.shape[1]
        or not 3 <= len(cost) <= 13
        or (cost < 0).any()
        or np.any(np.diag(cost) != 0)
        or demand.shape != (len(cost),)
        or demand[0] != 0
        or (demand[1:] <= 0).any()
    ):
        raise ValueError(
            "CVRP requires 2..12 positive-demand customers, depot zero, nonnegative square costs and zero diagonal."
        )
    if (demand[1:] < 0.001).any() or (demand > 1e6).any() or (cost > 1e6).any():
        raise ValueError("Rescale route demands/costs into the supported numerical range.")
    capacity = number(spec["vehicle_capacity"], "vehicle_capacity", 0.001, 1e6)
    vehicles = integer(spec["vehicles"], "vehicles", 1, 4)
    limit = number(spec.get("time_limit_seconds", 10), "time_limit_seconds", 0.01, 60)
    n = len(cost)
    customers = range(1, n)
    edges = [(v, i, j) for v in range(vehicles) for i in range(n) for j in range(n) if i != j]
    index = {edge: p for p, edge in enumerate(edges)}
    orders = {(v, i): len(edges) + v * (n - 1) + i - 1 for v in range(vehicles) for i in customers}
    size = len(edges) + len(orders)
    c = [cost[i, j] for _, i, j in edges] + [0] * len(orders)
    lower = [0] * len(edges) + [1] * len(orders)
    upper = [1] * len(edges) + [n - 1] * len(orders)
    rows = []
    lo = []
    hi = []

    def add(terms, a, b):
        row = np.zeros(size)
        for key, value in terms:
            row[key] += value
        rows.append(row)
        lo.append(a)
        hi.append(b)

    for i in customers:
        add([(index[v, i, j], 1) for v in range(vehicles) for j in range(n) if j != i], 1, 1)
    for v in range(vehicles):
        add([(index[v, 0, j], 1) for j in customers], 0, 1)
        for i in range(n):
            add(
                [(index[v, i, j], 1) for j in range(n) if j != i] + [(index[v, j, i], -1) for j in range(n) if j != i],
                0,
                0,
            )
        add([(index[v, i, j], demand[i]) for i in customers for j in range(n) if j != i], 0, capacity)
        for i in customers:
            for j in customers:
                if i != j:
                    add([(orders[v, j], 1), (orders[v, i], -1), (index[v, i, j], -(n - 1))], 2 - n, np.inf)
    status, x = _solve(c, lower, upper, [1] * len(edges) + [0] * len(orders), rows, lo, hi, limit)
    output = {
        "status": status,
        "routes": [],
        "total_cost": None,
        "feasibility_checked": False,
        "baseline_changed": False,
    }
    if x is None:
        return output
    visits = []
    total = 0.0
    for v in range(vehicles):
        outgoing = {i: j for vv, i, j in edges if vv == v and x[index[v, i, j]] > 0.5}
        if 0 not in outgoing:
            continue
        route = [0]
        while True:
            nxt = outgoing[route[-1]]
            route.append(nxt)
            if nxt == 0:
                break
            if len(route) > n or nxt in route[:-1]:
                raise ValueError("Invalid disconnected route.")
        route_cost = sum(cost[i, j] for i, j in zip(route, route[1:]))
        load = sum(demand[i] for i in route)
        if load > capacity + 1e-6 or len(outgoing) != len(route) - 1:
            raise ValueError("Disconnected or over-capacity route.")
        visits.extend(route[1:-1])
        total += route_cost
        output["routes"].append(
            {"vehicle_position": v, "node_positions": route, "load": float(load), "cost": float(route_cost)}
        )
    if sorted(visits) != list(customers):
        raise ValueError("Customer coverage is not exactly once.")
    output.update(total_cost=float(total), feasibility_checked=True)
    return output


def scheduling(spec):
    fields(spec, ("jobs",), ("release_times", "deadlines", "time_limit_seconds"))
    jobs = spec["jobs"]
    if not isinstance(jobs, list) or not 1 <= len(jobs) <= 15:
        raise ValueError("Supply 1..15 ordered job operation lists.")
    operations = []
    chains = []
    for job, tasks in enumerate(jobs):
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= 10:
            raise ValueError("Each job needs 1..10 operations.")
        chain = []
        for task in tasks:
            fields(task, ("machine", "duration"))
            machine = integer(task["machine"], "machine", 0, 9)
            duration = number(task["duration"], "duration", 0.001, 1e5)
            chain.append(len(operations))
            operations.append((job, machine, duration))
        chains.append(chain)
    if len(operations) > 60:
        raise ValueError("At most 60 operations are allowed.")
    release = real_array(spec.get("release_times", [0] * len(jobs)), "release_times")
    if release.shape != (len(jobs),) or (release < 0).any():
        raise ValueError("Release times must be nonnegative per job.")
    horizon = float(sum(t[2] for t in operations) + max(release))
    M = horizon
    deadlines = real_array(spec.get("deadlines", [horizon] * len(jobs)), "deadlines")
    if deadlines.shape != release.shape or (deadlines < release).any():
        raise ValueError("Deadlines must match jobs and follow release times.")
    pairs = [(i, j) for i, j in combinations(range(len(operations)), 2) if operations[i][1] == operations[j][1]]
    count = len(operations)
    size = count + 1 + len(pairs)
    c = [0] * size
    c[count] = 1
    lower = [release[job] for job, _, _ in operations] + [0] + [0] * len(pairs)
    upper = [horizon - duration for _, _, duration in operations] + [horizon] + [1] * len(pairs)
    rows = []
    lo = []
    hi = []

    def add(terms, a, b):
        row = np.zeros(size)
        for k, v in terms:
            row[k] += v
        rows.append(row)
        lo.append(a)
        hi.append(b)

    for i, (_, _, duration) in enumerate(operations):
        add([(count, 1), (i, -1)], duration, np.inf)
    for job, chain in enumerate(chains):
        for a, b in zip(chain, chain[1:]):
            add([(b, 1), (a, -1)], operations[a][2], np.inf)
        add([(chain[-1], 1)], -np.inf, deadlines[job] - operations[chain[-1]][2])
    for p, (i, j) in enumerate(pairs):
        order = count + 1 + p
        add([(j, 1), (i, -1), (order, -M)], operations[i][2] - M, np.inf)
        add([(i, 1), (j, -1), (order, M)], operations[j][2], np.inf)
    status, x = _solve(
        c,
        lower,
        upper,
        [0] * (count + 1) + [1] * len(pairs),
        rows,
        lo,
        hi,
        number(spec.get("time_limit_seconds", 10), "time_limit_seconds", 0.01, 60),
    )
    result = {
        "status": status,
        "operations": [],
        "makespan": None,
        "feasibility_checked": False,
        "baseline_changed": False,
    }
    if x is None:
        return result
    for i, (job, machine, duration) in enumerate(operations):
        result["operations"].append(
            {
                "operation_position": i,
                "job_position": job,
                "machine": machine,
                "start": float(x[i]),
                "end": float(x[i] + duration),
            }
        )
    for i, j in pairs:
        if not (x[i] + operations[i][2] <= x[j] + 1e-6 or x[j] + operations[j][2] <= x[i] + 1e-6):
            raise ValueError("Machine overlap in incumbent.")
    result.update(makespan=float(max(x[i] + op[2] for i, op in enumerate(operations))), feasibility_checked=True)
    return result


def _fronts(F, violation):
    size = len(F)
    dominates = [[] for _ in range(size)]
    degree = np.zeros(size, int)
    for i, j in combinations(range(size), 2):
        if violation[i] != violation[j] and (violation[i] > 0 or violation[j] > 0):
            a, b = (i, j) if violation[i] < violation[j] else (j, i)
        elif np.all(F[i] <= F[j]) and np.any(F[i] < F[j]):
            a, b = i, j
        elif np.all(F[j] <= F[i]) and np.any(F[j] < F[i]):
            a, b = j, i
        else:
            continue
        dominates[a].append(b)
        degree[b] += 1
    front = np.flatnonzero(degree == 0).tolist()
    fronts = []
    while front:
        fronts.append(front)
        following = []
        for i in front:
            for j in dominates[i]:
                degree[j] -= 1
                if degree[j] == 0:
                    following.append(j)
        front = following
    return fronts


def _crowding(F, front):
    distance = {i: 0.0 for i in front}
    for objective in range(F.shape[1]):
        order = sorted(front, key=lambda i: (F[i, objective], i))
        span = F[order[-1], objective] - F[order[0], objective]
        if span == 0:
            continue
        distance[order[0]] = distance[order[-1]] = np.inf
        for k in range(1, len(order) - 1):
            distance[order[k]] += (F[order[k + 1], objective] - F[order[k - 1], objective]) / span
    return distance


def evolutionary(spec):
    fields(spec, ("lower", "upper", "objectives", "seed"), ("population", "generations", "matrix", "constraint_upper"))
    lower = real_array(spec["lower"], "lower")
    upper = real_array(spec["upper"], "upper")
    if lower.ndim != 1 or not 1 <= len(lower) <= 10 or lower.shape != upper.shape or (lower >= upper).any():
        raise ValueError("Declare 1..10 finite nonempty decision bounds.")
    objectives = spec["objectives"]
    if not isinstance(objectives, list) or not 2 <= len(objectives) <= 4:
        raise ValueError("Declare 2..4 quadratic minimization objectives.")
    linear = []
    quadratic = []
    bias = []
    for obj in objectives:
        fields(obj, ("linear", "quadratic", "bias"))
        a = real_array(obj["linear"], "linear")
        q = real_array(obj["quadratic"], "quadratic")
        if a.shape != lower.shape or q.shape != a.shape:
            raise ValueError("Objective dimensions do not match bounds.")
        linear.append(a)
        quadratic.append(q)
        bias.append(number(obj["bias"], "bias"))
    A = real_array(spec.get("matrix", []), "matrix")
    constraint_rhs = real_array(spec.get("constraint_upper", []), "constraint_upper")
    if A.size and (A.ndim != 2 or A.shape[1] != len(lower) or A.shape[0] > 30 or constraint_rhs.shape != (len(A),)):
        raise ValueError("Invalid linear inequality matrix/bounds.")
    if not A.size and (A.ndim != 1 or constraint_rhs.size):
        raise ValueError("Constraint bounds require a matrix.")
    pop = integer(spec.get("population", 32), "population", 8, 80)
    generations = integer(spec.get("generations", 20), "generations", 1, 100)
    if pop % 2:
        raise ValueError("Population must be even.")
    rng = np.random.default_rng(integer(spec["seed"], "seed", 0, 2**32 - 1))
    span = upper - lower
    linear, quadratic, bias = np.asarray(linear), np.asarray(quadratic), np.asarray(bias)

    def evaluate(X):
        values = X @ linear.T + X**2 @ quadratic.T + bias
        violation = np.maximum(0, X @ A.T - constraint_rhs).sum(axis=1) if A.size else np.zeros(len(X))
        if not np.isfinite(values).all() or not np.isfinite(violation).all():
            raise ValueError("Rescale evolutionary objective/constraint coefficients to avoid overflow.")
        return values, violation

    X = rng.uniform(lower, upper, (pop, len(lower)))
    F, V = evaluate(X)
    for _ in range(generations):
        fronts = _fronts(F, V)
        rank = {i: k for k, f in enumerate(fronts) for i in f}
        crowd = {i: d for f in fronts for i, d in _crowding(F, f).items()}

        def tournament():
            a, b = rng.integers(0, len(X), 2)
            return X[min((a, b), key=lambda i: (rank[i], -crowd[i], i))].copy()

        children = []
        for _ in range(pop // 2):
            a, b = tournament(), tournament()
            # Simulated binary crossover, then bounded polynomial mutation (eta=15/20).
            u = rng.random(len(lower))
            beta = np.where(u <= 0.5, (2 * u) ** (1 / 16), (1 / (2 * (1 - u))) ** (1 / 16))
            for child in [((1 + beta) * a + (1 - beta) * b) / 2, ((1 - beta) * a + (1 + beta) * b) / 2]:
                child = np.clip(child, lower, upper)
                for j in range(len(lower)):
                    if rng.random() < 1 / len(lower):
                        u = rng.random()
                        d1 = (child[j] - lower[j]) / span[j]
                        d2 = (upper[j] - child[j]) / span[j]
                        delta = (
                            (2 * u + (1 - 2 * u) * (1 - d1) ** 21) ** (1 / 21) - 1
                            if u < 0.5
                            else 1 - (2 * (1 - u) + 2 * (u - 0.5) * (1 - d2) ** 21) ** (1 / 21)
                        )
                        child[j] += delta * span[j]
                children.append(np.clip(child, lower, upper))
        pool = np.vstack([X, children])
        child_F, child_V = evaluate(np.asarray(children))
        F, V = np.vstack([F, child_F]), np.r_[V, child_V]
        selected = []
        for front in _fronts(F, V):
            crowd = _crowding(F, front)
            selected += sorted(front, key=lambda i: (-crowd[i], i))[: pop - len(selected)]
            if len(selected) == pop:
                break
        X, F, V = pool[selected], F[selected], V[selected]
    front = [i for i in _fronts(F, V)[0] if V[i] <= 1e-8]
    unique = sorted(set(tuple(X[i]) for i in front))
    solutions = np.asarray(unique).reshape(-1, len(lower))
    lookup = {tuple(X[i]): F[i] for i in front}
    objective_values = np.asarray([lookup[point] for point in unique]).reshape(-1, len(objectives))
    return {
        "algorithm": "Constrained NSGA-II with nondominated sorting, crowding, tournament selection, simulated binary crossover and polynomial mutation",
        "status": "feasible_front" if unique else "no_feasible_solution_found",
        "solutions": solutions.tolist(),
        "objectives": objective_values.tolist(),
        "population": pop,
        "generations": generations,
        "objective_evaluations": pop * (generations + 1),
        "optimality_proven": False,
        "baseline_changed": False,
    }
