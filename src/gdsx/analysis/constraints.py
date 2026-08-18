"""From measurement to a solvable system.

`sim.sensitivity.map` tells you, per watched element, which cycles a
single-pulse perturbation moves it. That is a fact about the circuit, not yet
a puzzle: turning it into one means saying how many of those cycles must
actually fire, which is a constraint the *investigator* states, not something
`sensitivity.map` can infer on its own.

`System` is that constraint set: a list of rows, each one "exactly (or at
most) `k` of these candidate cycles are chosen", built up from
`from_sensitivity` and whatever other constraint families the investigation
found (epoch slots, spacing rules -- anything expressible as a linear bound
over a subset of cycles). It is deliberately one shape for every family,
because a bespoke shape per family is how a constraint quietly gets left out.

`unconstrained_elements()` is the check that catches that: a watched element
with no row naming it is a fact measured but never stated, and every row it
should have had is a trap the solve below will walk straight past.

`method="dfs"` is a plain backtracking search with no dependencies. `ilp`
hands the same rows to `scipy.optimize.milp` and needs `scipy` installed (the
`solve` extra) -- `dfs` must keep working without it.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..sim.sensitivity import SensitivityMap

Solution = tuple[int, ...]


@dataclass(frozen=True)
class Constraint:
    """One linear row: `lb <= sum(x[e] for e in elements) <= ub`.

    Every constraint family this kind of investigation has needed turned out
    to be one of two shapes: `exact` ("this counter is hit exactly twice",
    "this epoch slot gets exactly two pulses") or `at_most` ("these two
    cycles cannot both fire", `lb=0, ub=1` over just the forbidden pair).
    """

    name: str
    elements: tuple[int, ...]
    lb: int
    ub: int

    @classmethod
    def exact(
        cls, name: str, elements: list[int] | tuple[int, ...], k: int
    ) -> "Constraint":
        return cls(name, tuple(elements), k, k)

    @classmethod
    def at_most(
        cls, name: str, elements: list[int] | tuple[int, ...], k: int
    ) -> "Constraint":
        return cls(name, tuple(elements), 0, k)


@dataclass(frozen=True)
class System:
    """A constraint set over a shared pool of candidate cycles (`variables`).

    `watched` is every element the investigation is tracking, not just the
    ones that already have a row, so `unconstrained_elements()` has
    something to check against.
    """

    variables: tuple[int, ...]
    watched: tuple[str, ...]
    constraints: tuple[Constraint, ...]

    def with_constraint(self, constraint: Constraint) -> "System":
        variables = tuple(sorted(set(self.variables) | set(constraint.elements)))
        return System(variables, self.watched, self.constraints + (constraint,))

    def unconstrained_elements(self) -> tuple[str, ...]:
        """Watched elements with no row naming them -- measured, never stated."""
        named = {c.name for c in self.constraints}
        return tuple(name for name in self.watched if name not in named)

    def to_ilp(self):
        """`(A, lb, ub)`, ready for `scipy.optimize.LinearConstraint(A, lb, ub)`."""
        try:
            import numpy as np
        except ImportError as exc:
            raise ImportError(
                "System.to_ilp() needs numpy -- install the 'solve' extra "
                "(`uv sync --extra solve`)"
            ) from exc

        index = {v: i for i, v in enumerate(self.variables)}
        a = np.zeros((len(self.constraints), len(self.variables)))
        lb = np.zeros(len(self.constraints))
        ub = np.zeros(len(self.constraints))
        for r, c in enumerate(self.constraints):
            for e in c.elements:
                a[r, index[e]] = 1
            lb[r] = c.lb
            ub[r] = c.ub
        return a, lb, ub

    def to_exact_cover(self) -> dict[str, tuple[int, ...]]:
        """`{row name: candidate elements}` for rows where `lb == ub`.

        A spacing rule (`lb=0, ub=1`) is a bound, not an exact-cover item, and
        is left out here, read it back with `to_ilp()` or `to_dimacs()`.
        """
        return {c.name: c.elements for c in self.constraints if c.lb == c.ub}

    def to_dimacs(self) -> str:
        """A DIMACS CNF encoding of every row, for anyone who wants a SAT solver.

        One boolean variable per element of `variables` (1-indexed, in that
        order), plus whatever auxiliary variables the sequential-counter
        cardinality encoding (Sinz 2005) needs for each row's bound: O(n * k)
        clauses instead of the exponential blow-up of listing every forbidden
        subset directly.
        """
        index = {v: i + 1 for i, v in enumerate(self.variables)}
        next_var = [len(self.variables) + 1]
        clauses: list[list[int]] = []
        for c in self.constraints:
            lits = [index[e] for e in c.elements]
            if c.ub < len(lits):
                clauses += _at_most_k(lits, c.ub, next_var)
            if c.lb > 0:
                # "at least lb of lits" == "at most (n - lb) of the negations"
                clauses += _at_most_k(
                    [-lit for lit in lits], len(lits) - c.lb, next_var
                )
        nvars = next_var[0] - 1
        lines = [f"p cnf {nvars} {len(clauses)}"]
        lines += [" ".join(str(lit) for lit in clause) + " 0" for clause in clauses]
        return "\n".join(lines) + "\n"

    def solve(self, method: str = "dfs", limit: int = 1) -> list[Solution]:
        if method == "dfs":
            return _solve_dfs(self, limit)
        if method == "ilp":
            return _solve_ilp(self, limit)
        raise ValueError(f"unknown method {method!r}: use 'dfs' or 'ilp'")

    def count(self, limit: int = 1000) -> int:
        """How many solutions exist, capped at `limit` -- "is this unique?"
        without committing to enumerate an unbounded search."""
        return len(self.solve(method="dfs", limit=limit))


def from_sensitivity(smap: SensitivityMap, targets: dict[str, int]) -> System:
    """One `exact` row per name in `targets`, over the cycles `smap` found it
    reacts to. Every other element `smap` watched is carried through as
    `watched` but left unconstrained -- add its row yourself, or
    `unconstrained_elements()` will name it for you.
    """
    constraints = tuple(
        Constraint.exact(name, smap.cycles_for(name), k) for name, k in targets.items()
    )
    variables = tuple(sorted({e for c in constraints for e in c.elements}))
    return System(variables=variables, watched=smap.watch, constraints=constraints)


#! dfs


def _solve_dfs(system: System, limit: int) -> list[Solution]:
    variables = system.variables
    constraints = system.constraints
    members = [set(c.elements) for c in constraints]

    # remaining[r][i] = how many of variables[i:] are candidates for row r,
    # the slack left to still reach lb, and the ceiling on how high we could
    # still push past ub.
    remaining = []
    for members_r in members:
        rem = [0] * (len(variables) + 1)
        for i in range(len(variables) - 1, -1, -1):
            rem[i] = rem[i + 1] + (1 if variables[i] in members_r else 0)
        remaining.append(rem)

    found: list[Solution] = []
    chosen: list[int] = []
    counts = [0] * len(constraints)

    def feasible(i: int) -> bool:
        for r, c in enumerate(constraints):
            if counts[r] > c.ub or counts[r] + remaining[r][i] < c.lb:
                return False
        return True

    def rec(i: int) -> None:
        if len(found) >= limit:
            return
        if i == len(variables):
            if all(c.lb <= counts[r] <= c.ub for r, c in enumerate(constraints)):
                found.append(tuple(sorted(chosen)))
            return
        v = variables[i]
        rows = [r for r in range(len(constraints)) if v in members[r]]
        for take in (1, 0):
            if take:
                if any(counts[r] + 1 > constraints[r].ub for r in rows):
                    continue
                for r in rows:
                    counts[r] += 1
                chosen.append(v)
            if feasible(i + 1):
                rec(i + 1)
            if take:
                for r in rows:
                    counts[r] -= 1
                chosen.pop()
            if len(found) >= limit:
                return

    rec(0)
    return found


#! ilp


def _solve_ilp(system: System, limit: int) -> list[Solution]:
    try:
        import numpy as np
        from scipy.optimize import Bounds, LinearConstraint, milp
    except ImportError as exc:
        raise ImportError(
            "System.solve(method='ilp') needs scipy -- install the 'solve' "
            "extra (`uv sync --extra solve`), or use method='dfs'"
        ) from exc

    a, lb, ub = system.to_ilp()
    res = milp(
        c=np.ones(len(system.variables)),
        integrality=np.ones(len(system.variables)),
        bounds=Bounds(0, 1),
        constraints=LinearConstraint(a, lb=lb, ub=ub),
    )
    if not res.success:
        return []
    chosen = tuple(sorted(v for v, x in zip(system.variables, res.x) if x > 0.5))
    return [chosen]


#! dimacs


def _at_most_k(lits: list[int], k: int, next_var: list[int]) -> list[list[int]]:
    """Sequential-counter "at most k of `lits`" clauses (Sinz 2005).

    `next_var` is a one-element list holding the next unused DIMACS variable
    number; auxiliary variables are minted from it and it is advanced in
    place, so callers building several rows into one CNF share one pool.
    """
    n = len(lits)
    if k >= n:
        return []
    if k == 0:
        return [[-lit] for lit in lits]

    # s[i][j] (i in 0..n-2, j in 0..k-1) means "at least j+1 of lits[0..i] are true"
    s = [[0] * k for _ in range(n - 1)]
    for i in range(n - 1):
        for j in range(k):
            s[i][j] = next_var[0]
            next_var[0] += 1

    clauses: list[list[int]] = [[-lits[0], s[0][0]]]
    clauses += [[-s[0][j]] for j in range(1, k)]
    for i in range(1, n - 1):
        clauses.append([-lits[i], s[i][0]])
        clauses.append([-s[i - 1][0], s[i][0]])
        for j in range(1, k):
            clauses.append([-lits[i], -s[i - 1][j - 1], s[i][j]])
            clauses.append([-s[i - 1][j], s[i][j]])
        clauses.append([-lits[i], -s[i - 1][k - 1]])
    clauses.append([-lits[n - 1], -s[n - 2][k - 1]])
    return clauses
