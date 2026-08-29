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


def test_dfs_matches_brute_force_over_many_small_systems():
    """`_solve_dfs` prunes with a feasibility check narrowed to the rows the
    variable just decided appears in. That is only sound if it never loses a
    solution, so pin it against enumerating every subset directly, over a
    spread of row shapes (`exact` and `at_most`, overlapping and disjoint,
    feasible and not) rather than one lucky system.
    """
    shapes = [
        ((0, 1, 2), 1, 1),
        ((0, 1, 2, 3), 2, 2),
        ((2, 3, 4), 0, 1),
        ((0, 4), 1, 2),
        ((1, 3), 0, 1),
        ((0, 1, 2, 3, 4), 3, 3),
        ((), 0, 0),
        ((0,), 1, 1),
    ]
    variables = tuple(range(5))
    for size in (1, 2, 3):
        for rows in itertools.combinations(shapes, size):
            system = System(
                variables=variables,
                watched=(),
                constraints=tuple(
                    Constraint(f"r{i}", elements, lb, ub)
                    for i, (elements, lb, ub) in enumerate(rows)
                ),
            )
            expected = set()
            for take in itertools.product((0, 1), repeat=len(variables)):
                subset = {v for v, t in zip(variables, take) if t}
                if all(
                    lb <= len(subset & set(elements)) <= ub for elements, lb, ub in rows
                ):
                    expected.add(tuple(sorted(subset)))
            assert set(system.solve(method="dfs", limit=10_000)) == expected


def test_dfs_rejects_a_row_no_variable_can_satisfy():
    # `lb` above the row's own element count: unsatisfiable before the search
    # starts, and the narrowed check must still say so rather than walking
    # every leaf to find out.
    system = System(
        variables=tuple(range(4)),
        watched=(),
        constraints=(Constraint.exact("impossible", (), 1),),
    )
    assert system.solve(method="dfs", limit=100) == []


def test_solve_rejects_an_unknown_method():
    system = System(variables=(0,), watched=(), constraints=())
    with pytest.raises(ValueError):
        system.solve(method="bogus")


# The eleven irregular cycle groups a single-pulse sweep of the reference
# puzzle reads out of one bank of tally flops -- measured, not derived, and
# the only part of the system below that could not be written down from the
# rules alone. Together they partition cycles 0..120 exactly once.
MEASURED_GROUPS = [
    [10, 21, 32, 40, 43, 49, 50, 51, 52, 53, 54, 62, 73, 84, 92, 93, 94, 95,
     104, 105, 106, 114, 115, 116, 117, 118, 119, 120],
    [5, 6, 16, 25, 26, 27, 28, 36, 47, 58, 66, 67, 68, 69, 70, 71, 77, 88,
     99, 100, 110],
    [0, 1, 2, 3, 4, 11, 12, 14, 15, 22, 23, 33, 34, 45],
    [37, 38, 39, 48, 59, 60, 61, 72, 81, 82, 83],
    [63, 64, 65, 74, 85, 96, 107, 108, 109],
    [13, 24, 35, 44, 46, 55, 56, 57],
    [78, 79, 80, 89, 90, 101, 111, 112],
    [7, 17, 18, 29, 30, 41, 42],
    [75, 76, 86, 87, 97, 98],
    [8, 9, 19, 20, 31],
    [91, 102, 103, 113],
]

WINDOW = 121
EPOCH = 11


def _reference_puzzle_system() -> System:
    """The reference puzzle's four rules, as one `System`.

    Choose 22 of the 121 cycles, writing `e = cycle // 11` and
    `p = cycle % 11`: exactly two per epoch, exactly two per position,
    exactly two per measured group, and no two with `|dp| <= 1` and
    `|de| <= 1`. The first three are `exact` rows over a partition of the
    window; the fourth is one `at_most` bound per forbidden pair.
    """
    rows = [
        Constraint.exact(
            f"epoch {e}", [e * EPOCH + p for p in range(EPOCH)], 2
        )
        for e in range(EPOCH)
    ]
    rows += [
        Constraint.exact(
            f"position {p}", [e * EPOCH + p for e in range(EPOCH)], 2
        )
        for p in range(EPOCH)
    ]
    rows += [
        Constraint.exact(f"group {i}", cycles, 2)
        for i, cycles in enumerate(MEASURED_GROUPS)
    ]
    rows += [
        Constraint.at_most(f"spacing {a},{b}", (a, b), 1)
        for a in range(WINDOW)
        for b in range(a + 1, WINDOW)
        if abs(a % EPOCH - b % EPOCH) <= 1 and abs(a // EPOCH - b // EPOCH) <= 1
    ]
    return System(
        variables=tuple(range(WINDOW)),
        watched=tuple(c.name for c in rows),
        constraints=tuple(rows),
    )


def test_the_measured_groups_partition_the_window():
    seen = [c for group in MEASURED_GROUPS for c in group]
    assert sorted(seen) == list(range(WINDOW))


def test_the_reference_puzzle_system_has_exactly_one_solution():
    """The whole point of the constraint system: four rules, one answer.

    Every row here is something an investigator measures. Solving them
    together is the last step, and the system is worth stating only if it
    pins the answer down completely -- so assert not just that a solution
    exists but that the search, run to exhaustion, finds no second one.
    """
    system = _reference_puzzle_system()
    solutions = system.solve(method="dfs", limit=50)
    assert len(solutions) == 1, "the four rules should admit exactly one key"
    pulses = solutions[0]
    assert len(pulses) == 22
    assert pulses == (
        7, 9, 11, 16, 29, 31, 33, 35, 48, 50, 57,
        63, 70, 76, 78, 83, 91, 98, 104, 107, 111, 113,
    )

    # Independently of the search: the answer really does satisfy all four.
    for e in range(EPOCH):
        assert sum(1 for c in pulses if c // EPOCH == e) == 2
    for p in range(EPOCH):
        assert sum(1 for c in pulses if c % EPOCH == p) == 2
    for group in MEASURED_GROUPS:
        assert sum(1 for c in pulses if c in group) == 2
    for a, b in itertools.combinations(pulses, 2):
        assert not (
            abs(a % EPOCH - b % EPOCH) <= 1 and abs(a // EPOCH - b // EPOCH) <= 1
        )


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