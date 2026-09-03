"""Planning a claim: what gets settled here, what gets scheduled, what is refused

The verdicts this module can reach are the structural ones, and the tests below
are mostly about the boundary: a claim that is false must come back `DISPROVEN`
rather than as a form error, a claim that is malformed must come back as a form
error rather than as a verdict, and a claim nobody can settle must come back
`UNKNOWN` with a reason rather than as a guess.

The one thing that must never happen is a `PROVEN` that was not earned, so
`test_nothing_here_ever_proves_by_sampling` walks every planner and asserts that
every verdict this module emits is either structural or not a proof at all.
"""

from __future__ import annotations

import pytest

from gdsx.analysis import claims
from gdsx.core.graph import Graph
from gdsx.sim import compile
from gdsx.sim import slice as sl


@pytest.fixture(scope="module")
def design(puzzle_netlist):
    graph = Graph.of(puzzle_netlist)
    return graph, compile(puzzle_netlist)


@pytest.fixture(scope="module")
def a_flop(design):
    _, tape = design
    return tape.flop_names[0]


def leaves_of(design, flop):
    graph, tape = design
    found = set()
    for leaf in graph.d_support(flop):
        found.add(sl.flop_q_nets(tape, [leaf])[0] if leaf in graph.seq else leaf)
    return sorted(found)


#! structural


def test_structural_proves_the_real_driver(design):
    graph, tape = design
    driver = graph.driver_of("success")
    plan = claims.plan(graph, tape, {"kind": "structural", "net": "success", "cell": driver.cell})
    assert plan.verdict.kind == "PROVEN"
    assert plan.verdict.method == "structural"
    assert plan.verdict.cases == 1
    assert plan.job is None


def test_structural_accepts_any_of_the_three_spellings(design):
    """The UI shows a cell three ways; rejecting two of them would be pedantry"""
    from gdsx.functions import base_name, generic_name

    graph, tape = design
    driver = graph.driver_of("success")
    for spelling in (driver.cell, base_name(driver.cell), generic_name(driver.cell)):
        plan = claims.plan(
            graph, tape, {"kind": "structural", "net": "success", "cell": spelling}
        )
        assert plan.verdict.kind == "PROVEN", spelling


def test_a_wrong_driver_is_disproven_not_rejected(design):
    graph, tape = design
    plan = claims.plan(graph, tape, {"kind": "structural", "net": "success", "cell": "nand2"})
    assert plan.verdict.kind == "DISPROVEN"
    assert plan.verdict.expected == ("nand2",)
    assert plan.verdict.observed != ("nand2",)


def test_a_net_that_does_not_exist_is_a_form_error(design):
    graph, tape = design
    with pytest.raises(claims.ClaimError) as caught:
        claims.plan(graph, tape, {"kind": "structural", "net": "n_nope", "cell": "nand2"})
    assert caught.value.code == "unknown_net"


#! support


def test_structural_support_is_an_exact_set_compare(design, a_flop):
    graph, tape = design
    exact = leaves_of(design, a_flop)
    plan = claims.plan(graph, tape, {"kind": "support", "flop": a_flop, "leaves": exact})
    assert plan.verdict.kind == "PROVEN"
    assert plan.verdict.cases == len(exact)

    short = claims.plan(
        graph, tape, {"kind": "support", "flop": a_flop, "leaves": exact[:-1]}
    )
    assert short.verdict.kind == "DISPROVEN"
    assert set(short.verdict.observed) == set(exact)

    extra = claims.plan(
        graph, tape, {"kind": "support", "flop": a_flop, "leaves": [*exact, "clk"]}
    )
    assert extra.verdict.kind == "DISPROVEN"


def test_functional_support_is_a_different_claim_and_gets_a_job(design, a_flop):
    """Structural support is a graph fact; functional support is not

    A net can sit in the structural cone and never change the output. Answering
    the functional question with the structural fact would quietly let a player
    call one the other.
    """
    graph, tape = design
    plan = claims.plan(
        graph,
        tape,
        {"kind": "support", "flop": a_flop, "leaves": leaves_of(design, a_flop),
         "sense": "functional"},
    )
    assert plan.verdict is None
    assert plan.job.check == "essential"
    assert plan.job.engine == "combinational"
    assert plan.job.design.n_free == len(leaves_of(design, a_flop))


#! function


def test_function_cuts_both_sides_over_one_frontier(design, a_flop):
    graph, tape = design
    leaves = leaves_of(design, a_flop)
    plan = claims.plan(
        graph,
        tape,
        {"kind": "function", "flop": a_flop, "expression": " & ".join(leaves[:2])},
    )
    job = plan.job
    assert job.check == "equal"
    # The contract that makes a counterexample decodable: same names, same order.
    assert job.design.free == job.claim.free
    assert job.design.free_ids != () and job.claim.free_ids == tuple(
        range(len(job.claim.free))
    )


def test_an_expression_may_name_nets_the_cone_does_not_read(design, a_flop):
    """Claiming a dependency that is not there is a claim, not a typo

    It should come back disproven with a counterexample, which means the variable
    has to survive into the comparison rather than being rejected up front.
    """
    graph, tape = design
    plan = claims.plan(
        graph, tape, {"kind": "function", "flop": a_flop, "expression": "O[7]"}
    )
    assert "O[7]" in plan.job.design.free
    assert "O[7]" in plan.job.claim.free


def test_the_two_sides_agree_where_the_expression_is_the_truth(design):
    """A round trip through the parser and the expression compiler

    Read a real cone's truth table off the tape, write it back out as a
    sum-of-products over the same nets, and claim that. The two slices must agree
    on every assignment. If `expression_slice` disagreed with the tape about what
    `&` means, or if the parser bound a variable to the wrong net, this is where
    it would show -- and it is the closest thing to an end-to-end test of that
    path that does not need the browser.
    """
    graph, tape = design
    ctx = claims._Context(graph, tape)

    checked = 0
    for flop in tape.flop_names:
        cut = sl.of(tape, [ctx.d_net(flop)])
        if not 2 <= cut.n_free <= 4:
            continue
        text = _sum_of_products(cut)
        if text is None:
            continue

        plan = claims.plan(
            graph, tape, {"kind": "function", "flop": flop, "expression": text}
        )
        job = plan.job
        assert job.design.free == job.claim.free
        for row in range(job.design.cases):
            assignment = [(row >> i) & 1 for i in range(job.design.n_free)]
            assert sl.run(job.design, assignment) == sl.run(job.claim, assignment), text
        checked += 1
        if checked == 8:
            break
    assert checked >= 3, "expected several narrow cones in this design"


def _sum_of_products(cut):
    """`cut`'s truth table written back out as an expression over its own nets

    None when the function is a constant, which has no minterms to write and is
    not an interesting round trip anyway.
    """
    minterms = []
    for row in range(cut.cases):
        assignment = [(row >> i) & 1 for i in range(cut.n_free)]
        if sl.run(cut, assignment)[0] != 1:
            continue
        minterms.append(
            "("
            + " & ".join(
                net if value else f"~{net}" for net, value in zip(cut.free, assignment)
            )
            + ")"
        )
    if not minterms or len(minterms) == cut.cases:
        return None
    return " | ".join(minterms)


#! role


def test_role_pins_everything_outside_the_group_and_says_so(design):
    graph, tape = design
    group = list(tape.flop_names[:8])
    plan = claims.plan(
        graph, tape, {"kind": "role", "group": group, "role": "counter", "width": 8}
    )
    job = plan.job
    assert job.engine == "transition"
    assert job.design.n_free == 8, "the group's own Q nets and nothing else"
    assert len(job.design.targets) == 8
    assert any(note.startswith("holds ") for note in plan.notes)


def test_role_keeps_the_group_in_the_order_it_was_given(design):
    """Bit position 0 is the first flop named, not the alphabetically first"""
    graph, tape = design
    group = list(tape.flop_names[:6])
    forward = claims.plan(
        graph, tape, {"kind": "role", "group": group, "role": "counter", "width": 6}
    )
    backward = claims.plan(
        graph,
        tape,
        {"kind": "role", "group": group[::-1], "role": "counter", "width": 6},
    )
    assert forward.job.design.free == backward.job.design.free[::-1]


def test_role_rejects_a_width_that_does_not_match(design):
    graph, tape = design
    with pytest.raises(claims.ClaimError) as caught:
        claims.plan(
            graph,
            tape,
            {"kind": "role", "group": list(tape.flop_names[:4]), "role": "counter", "width": 8},
        )
    assert caught.value.code == "bad_claim"


def test_role_rejects_an_unknown_role(design):
    graph, tape = design
    with pytest.raises(claims.ClaimError):
        claims.plan(
            graph,
            tape,
            {"kind": "role", "group": [tape.flop_names[0]], "role": "gray-counter", "width": 1},
        )


#! invariant, requirement, timing, constraint


def test_a_frame_claim_about_a_flop_output_steps_to_its_d(design):
    """Inside one frame a flop's Q is a free variable, not a function

    Answering the question as asked would be honest and useless. Stepping to D is
    the question the player meant, and the note says so rather than the step
    happening invisibly.
    """
    graph, tape = design
    plan = claims.plan(
        graph,
        tape,
        {"kind": "invariant", "net": "success", "value": 1, "condition": "I & enable"},
    )
    assert plan.job.check == "implies"
    assert "success" not in plan.job.design.target_names
    assert any("one cycle earlier" in note for note in plan.notes)


def test_a_sequential_invariant_says_up_front_that_it_cannot_be_proven(design):
    graph, tape = design
    plan = claims.plan(
        graph,
        tape,
        {
            "kind": "invariant",
            "net": "success",
            "value": 1,
            "condition": "I & enable",
            "regime": "sequential",
        },
    )
    assert plan.job.engine == "sequential"
    assert plan.job.design is None
    assert any("cannot prove" in note for note in plan.notes)


def test_requirement_falls_through_to_a_search_when_not_forced(design, a_flop):
    graph, tape = design
    plan = claims.plan(
        graph,
        tape,
        {"kind": "requirement", "output": "success", "value": 1,
         "flop": a_flop, "flop_value": 1},
    )
    assert plan.job.check == "requirement"
    # Both the output side and the flop side are read back from one slice, so a
    # counterexample is one assignment rather than two runs that might disagree.
    assert len(plan.job.design.targets) == 2


def test_requirement_is_disproven_when_justification_forces_the_opposite(design):
    """Backward justification finding the other value is a disproof, not a miss"""
    graph, tape = design
    from gdsx.analysis import justify

    ctx = claims._Context(graph, tape)
    by_q = {ctx.q_net(instance): instance for instance in graph.seq}

    # Searched rather than named: net names are only stable after the canonical
    # sort, and a test that hardcodes one goes quietly wrong the first time the
    # numbering moves. `success` itself is no use here -- its D is an OR, so
    # justification stops at the disjunction and forces nothing, which is why the
    # search fallback in the planner exists at all.
    found = None
    for net in sorted(graph.netlist.nets):
        if graph.driver_of(net) is None:
            continue
        try:
            candidate = justify.requirements(graph, net, 1)
        except justify.Unenumerable:
            continue
        hits = [q for q in candidate.forced if q in by_q]
        if hits:
            found = (net, candidate, hits[0])
            break
    assert found is not None, "no net in this design forces a flop's Q"

    net, requirements, q = found
    flop, value = by_q[q], requirements.forced[q]

    right = claims.plan(
        graph,
        tape,
        {"kind": "requirement", "output": net, "value": 1,
         "flop": flop, "flop_value": value},
    )
    assert right.verdict.kind == "PROVEN"
    assert right.verdict.method == "structural"
    assert right.job is None

    wrong = claims.plan(
        graph,
        tape,
        {"kind": "requirement", "output": net, "value": 1,
         "flop": flop, "flop_value": 1 - value},
    )
    assert wrong.verdict.kind == "DISPROVEN"
    assert wrong.verdict.observed == (f"{q} == {value}",)


def test_timing_records_that_it_is_about_one_stimulus(design):
    graph, tape = design
    plan = claims.plan(
        graph,
        tape,
        {"kind": "timing", "net": "success", "event": "latches-high", "cycles": [121]},
    )
    assert plan.job.engine == "sequential"
    assert any("stimulus" in note for note in plan.notes)


def test_timing_rejects_an_event_it_cannot_check(design):
    graph, tape = design
    with pytest.raises(claims.ClaimError):
        claims.plan(
            graph,
            tape,
            {"kind": "timing", "net": "success", "event": "glitches", "cycles": [1]},
        )


def test_constraint_parses_its_predicate_once(design):
    graph, tape = design
    plan = claims.plan(
        graph,
        tape,
        {
            "kind": "constraint",
            "output": "success",
            "predicate": "count(I) == 22 & mingap(I) >= 2",
        },
    )
    tree = plan.job.predicate
    assert tree["node"] == "and"
    assert {t["measure"] for t in tree["of"]} == {"count", "mingap"}
    assert all(t["port"] == "I" for t in tree["of"])


#! expressions and predicates


def test_expressions_accept_both_spellings_of_not(design):
    graph, _ = design
    nets = graph.netlist.nets
    assert claims.parse_expression("~I", nets) == claims.parse_expression("!I", nets)


def test_expressions_accept_bus_indices(design):
    """`O[7]` is a real net name and Liberty's own tokeniser cannot read one"""
    graph, _ = design
    parsed = claims.parse_expression("O[7] & ~O[0]", graph.netlist.nets)
    from gdsx.liberty import variables

    assert variables(parsed) == {"O[7]", "O[0]"}


def test_expressions_reject_a_net_that_does_not_exist(design):
    graph, _ = design
    with pytest.raises(claims.ClaimError) as caught:
        claims.parse_expression("I & not_a_net", graph.netlist.nets)
    assert caught.value.code == "unknown_net"


def test_expressions_reject_nonsense(design):
    graph, _ = design
    with pytest.raises(claims.ClaimError) as caught:
        claims.parse_expression("I & & enable", graph.netlist.nets)
    assert caught.value.code == "bad_expression"


def test_an_empty_expression_is_an_error_not_a_constant(design):
    graph, _ = design
    with pytest.raises(claims.ClaimError):
        claims.parse_expression("   ", graph.netlist.nets)


def test_expression_slice_evaluates_what_it_was_given(design):
    graph, _ = design
    expr = claims.parse_expression("I & ~enable", graph.netlist.nets)
    compiled = claims.expression_slice(expr, ["I", "enable"])
    assert compiled.free == ("I", "enable")
    assert [sl.run(compiled, [i, e])[0] for i in (0, 1) for e in (0, 1)] == [0, 0, 1, 0]


def test_expression_slice_keeps_variables_it_does_not_use(design):
    """The free list is the shared assignment order, not a usage report"""
    graph, _ = design
    expr = claims.parse_expression("I", graph.netlist.nets)
    compiled = claims.expression_slice(expr, ["clk", "I", "enable"])
    assert compiled.free == ("clk", "I", "enable")
    assert sl.run(compiled, [1, 0, 1]) == (0,)
    assert sl.run(compiled, [0, 1, 0]) == (1,)


def test_expression_slice_handles_constants(design):
    graph, _ = design
    for text, expected in (("1", 1), ("0", 0), ("I | 1", 1), ("I & 0", 0)):
        expr = claims.parse_expression(text, graph.netlist.nets)
        compiled = claims.expression_slice(expr, ["I"])
        assert sl.run(compiled, [0])[0] == expected, text


def test_predicates_reject_an_unknown_measurement(design):
    graph, _ = design
    with pytest.raises(claims.ClaimError) as caught:
        claims.parse_predicate("wobble(I) == 3", ["I"])
    assert caught.value.code == "bad_predicate"


def test_predicates_reject_a_port_that_is_not_an_input(design):
    with pytest.raises(claims.ClaimError) as caught:
        claims.parse_predicate("count(nope) == 3", ["I"])
    assert caught.value.code == "unknown_port"


def test_predicates_carry_their_window(design):
    tree = claims.parse_predicate("count(I, 0, 40) <= 3", ["I"])
    assert tree["node"] == "term"
    assert tree["window"] == [0, 40]
    assert tree["op"] == "<="


def test_a_predicate_with_no_comparison_is_refused(design):
    with pytest.raises(claims.ClaimError):
        claims.parse_predicate("I & enable", ["I", "enable"])


#! the rule the whole module exists to keep


def test_nothing_here_ever_proves_by_sampling(design, a_flop):
    """Every verdict this module can emit is structural, or is not a proof

    The evaluator may return `LIKELY`; this module may not, and it may not return
    `PROVEN` off anything but a graph fact. Keeping that true here is what makes
    the split trustworthy: there is one place that can say "proven by sampling",
    and it does not exist.
    """
    graph, tape = design
    everything = [
        {"kind": "structural", "net": "success", "cell": "nand2"},
        {"kind": "structural", "net": "success", "cell": graph.driver_of("success").cell},
        {"kind": "support", "flop": a_flop, "leaves": leaves_of(design, a_flop)},
        {"kind": "support", "flop": a_flop, "leaves": [], "sense": "functional"},
        {"kind": "function", "flop": a_flop, "expression": "I"},
        {"kind": "role", "group": list(tape.flop_names[:4]), "role": "counter", "width": 4},
        {"kind": "invariant", "net": "success", "value": 1, "condition": "I"},
        {"kind": "requirement", "output": "success", "value": 1, "flop": a_flop, "flop_value": 1},
        {"kind": "timing", "net": "success", "event": "high", "cycles": [1]},
        {"kind": "constraint", "output": "success", "predicate": "count(I) == 22"},
    ]
    seen = set()
    for claim in everything:
        plan = claims.plan(graph, tape, claim)
        seen.add(plan.kind)
        assert (plan.verdict is None) != (plan.job is None), "one or the other"
        if plan.verdict is not None:
            assert plan.verdict.kind in ("PROVEN", "DISPROVEN", "UNKNOWN")
            assert plan.verdict.kind != "LIKELY"
            if plan.verdict.kind == "PROVEN":
                assert plan.verdict.method == "structural"
    assert seen == set(claims.KINDS)


def test_an_unknown_claim_kind_is_refused(design):
    graph, tape = design
    with pytest.raises(claims.ClaimError) as caught:
        claims.plan(graph, tape, {"kind": "vibes", "net": "success"})
    assert caught.value.code == "bad_claim"