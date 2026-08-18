"""`analysis.constraints`: measurement -> a solvable system.

The mechanics (DFS, DIMACS, `to_ilp`, `unconstrained_elements`) are checked
against small hand-built systems where the right answer is checkable by
inspection.
"""

from __future__ import annotations

import itertools

import pytest

from gdsx.analysis import constraints
from gdsx.analysis.constraints import Constraint, System
from gdsx.sim import sensitivity


def _brute_force_dimacs(dimacs: str) -> set[tuple[int, ...]]:
    """Every satisfying assignment's main variables (1..nmain aren't known
    here, so the caller passes `nmain` in separately -- see `_check_dimacs`)."""
    lines = dimacs.strip().split("\n")
    nvars = int(lines[0].split()[2])
    clauses = [[int(x) for x in line.split()[:-1]] for line in lines[1:]]
    sat = set()
    for bits in itertools.product((0, 1), repeat=nvars):
        assign = {i + 1: bool(b) for i, b in enumerate(bits)}
        if all(
            any((lit > 0) == assign[abs(lit)] for lit in clause) for clause in clauses
        ):
            sat.add(bits)
    return sat


def _check_dimacs(system: System) -> None:
    dimacs = system.to_dimacs()
    lines = dimacs.strip().split("\n")
    nvars = int(lines[0].split()[2])
    nmain = len(system.variables)
    sat = _brute_force_dimacs(dimacs)
    main_solutions = {tuple(bits[:nmain]) for bits in sat}
    expected = {
        tuple(1 if v in sol else 0 for v in system.variables)
        for sol in system.solve(method="dfs", limit=10_000)
    }
    assert main_solutions == expected
    assert nvars >= nmain


def test_from_sensitivity_builds_one_exact_row_per_target():
    smap = sensitivity.SensitivityMap(
        cycles=4,
        watch=("a", "b", "c"),
        hits={"a": (0, 1, 2), "b": (2, 3), "c": ()},
        by_cycle={0: ("a",), 1: ("a",), 2: ("a", "b"), 3: ("b",)},
        unreactive=("c",),
        silent_cycles=(),
    )
    system = constraints.from_sensitivity(smap, {"a": 2, "b": 1})
    assert system.watched == ("a", "b", "c")
    assert set(system.variables) == {0, 1, 2, 3}
    assert system.unconstrained_elements() == ("c",)


def test_unconstrained_elements_catches_a_watched_element_with_no_row():
    system = System(
        variables=(0, 1, 2),
        watched=("counter", "trap"),
        constraints=(Constraint.exact("counter", (0, 1, 2), 2),),
    )
    assert system.unconstrained_elements() == ("trap",)


def test_with_constraint_adds_a_row_and_extends_variables():
    system = System(variables=(0, 1), watched=("a",), constraints=())
    grown = system.with_constraint(Constraint.exact("a", (0, 1, 2), 1))
    assert grown.constraints == (Constraint.exact("a", (0, 1, 2), 1),)
    assert grown.variables == (0, 1, 2)
    assert system.constraints == ()  # unchanged: with_constraint returns a new System


def test_dfs_solves_a_small_exact_cover_shaped_system():
    # exactly 2 of {0,1,2}, exactly 1 of {2,3}: every solution by hand.
    system = System(
        variables=(0, 1, 2, 3),
        watched=("a", "b"),
        constraints=(
            Constraint.exact("a", (0, 1, 2), 2),
            Constraint.exact("b", (2, 3), 1),
        ),
    )
    solutions = set(system.solve(method="dfs", limit=100))
    assert solutions == {(0, 1, 3), (0, 2), (1, 2)}


def test_dfs_honours_an_at_most_spacing_rule():
    system = System(
        variables=tuple(range(5)),
        watched=("a", "spacing"),
        constraints=(
            Constraint.exact("a", (0, 1, 2, 3, 4), 2),
            Constraint.at_most("spacing01", (0, 1), 1),
        ),
    )
    for solution in system.solve(method="dfs", limit=1000):
        assert not (0 in solution and 1 in solution)


def test_dfs_respects_the_limit():
    system = System(
        variables=(0, 1, 2, 3),
        watched=(),
        constraints=(Constraint.exact("a", (0, 1, 2, 3), 2),),
    )
    assert len(system.solve(method="dfs", limit=2)) == 2


def test_count_reports_zero_for_an_infeasible_system():
    system = System(
        variables=(0, 1),
        watched=(),
        constraints=(Constraint.exact("a", (0, 1), 5),),
    )
    assert system.count() == 0


def test_to_ilp_matches_the_constraint_rows():
    pytest.importorskip("numpy")
    system = System(
        variables=(0, 1, 2),
        watched=("a",),
        constraints=(Constraint.exact("a", (0, 2), 1),),
    )
    a, lb, ub = system.to_ilp()
    assert a.shape == (1, 3)
    assert list(a[0]) == [1, 0, 1]
    assert list(lb) == [1]
    assert list(ub) == [1]


def test_to_exact_cover_excludes_at_most_rows():
    system = System(
        variables=(0, 1, 2),
        watched=("a", "spacing"),
        constraints=(
            Constraint.exact("a", (0, 1, 2), 1),
            Constraint.at_most("spacing", (0, 1), 1),
        ),
    )
    assert system.to_exact_cover() == {"a": (0, 1, 2)}


def test_dimacs_matches_dfs_solutions_for_exact_rows():
    system = System(
        variables=(0, 1, 2, 3),
        watched=("a", "b"),
        constraints=(
            Constraint.exact("a", (0, 1, 2), 2),
            Constraint.exact("b", (2, 3), 1),
        ),
    )
    _check_dimacs(system)


def test_dimacs_matches_dfs_solutions_with_an_at_most_row():
    system = System(
        variables=tuple(range(5)),
        watched=("a", "spacing"),
        constraints=(
            Constraint.exact("a", (0, 1, 2, 3, 4), 2),
            Constraint.at_most("spacing01", (0, 1), 1),
        ),
    )
    _check_dimacs(system)


def test_solve_rejects_an_unknown_method():
    system = System(variables=(0,), watched=(), constraints=())
    with pytest.raises(ValueError):
        system.solve(method="bogus")


@pytest.mark.slow
def test_reproduces_test_py_on_the_puzzle():
    """The exact formulation from original solution, through the new API: one
    exact-2 row per counter, solved with `method="ilp"` if scipy is
    available, else `"dfs"` -- both must agree on the pulse count.
    """
    counters = {
        "dfrtp_2_22 / dfrtp_2_23": [7, 17, 18, 29, 30, 41, 42],
        "dfrtp_2_19 / dfrtp_2_24": [8, 9, 19, 20, 31],
        "dfrtp_2_30 / dfrtp_2_31": [78, 79, 80, 89, 90, 101, 111, 112],
        "dfrtp_2_28 / dfrtp_2_29": [0, 1, 2, 3, 4, 11, 12, 14, 15, 22, 23, 33, 34, 45],
        "dfrtp_2_11 / dfrtp_2_12": [37, 38, 39, 48, 59, 60, 61, 72, 81, 82, 83],
        "dfrtp_2_7 / dfrtp_2_8": [13, 24, 35, 44, 46, 55, 56, 57],
        "dfrtp_2_13 / dfrtp_2_15": [91, 102, 103, 113],
        "dfrtp_2_14 / dfrtp_2_16": [63, 64, 65, 74, 85, 96, 107, 108, 109],
    }
    system = System(
        variables=tuple(sorted({c for cs in counters.values() for c in cs})),
        watched=tuple(counters),
        constraints=tuple(
            Constraint.exact(name, cs, 2) for name, cs in counters.items()
        ),
    )
    assert system.unconstrained_elements() == ()

    solutions = system.solve(method="dfs", limit=1)
    assert solutions, "the recovered constraints should be satisfiable"
    pulses = solutions[0]
    for name, cs in counters.items():
        assert sum(1 for c in pulses if c in cs) == 2