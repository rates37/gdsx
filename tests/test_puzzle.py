from __future__ import annotations

from gdsx import puzzle


def sequence_solution(port: str = "success") -> dict:
    return {
        "answer_kind": "sequence",
        "verify": {"predicate": {"type": "port_reaches_value_by_cycle", "port": port}},
    }


def test_tier0_reports_instance_and_sequential_counts(puzzle_netlist):
    assert puzzle._tier0_hint(puzzle_netlist) == "728 instances, 92 sequential"


def test_tier1_reports_ungated_registers(puzzle_netlist):
    text = puzzle._tier1_hint(puzzle_netlist)
    assert "of 92 registers have no recovered freeze condition" in text


def test_tier2_reports_register_groups(puzzle_netlist):
    text = puzzle._tier2_hint(puzzle_netlist)
    assert "register groups" in text
    assert "members" in text


def test_tier3_reports_the_success_d_cone(puzzle_netlist):
    text = puzzle._tier3_hint(puzzle_netlist, sequence_solution())
    assert (
        text == "success is driven by one flop (dfrtp_2_83); its D cone has 57 leaves"
    )


def test_tier3_is_none_for_a_non_sequence_puzzle(puzzle_netlist):
    assert puzzle._tier3_hint(puzzle_netlist, {"answer_kind": "function"}) is None


def test_generate_hints_covers_tiers_zero_through_three(puzzle_netlist):
    hints = puzzle.generate_hints(puzzle_netlist, sequence_solution())
    assert [h["tier"] for h in hints] == [0, 1, 2, 3]
    assert all(isinstance(h["text"], str) and h["text"] for h in hints)


def test_generate_hints_appends_an_authored_tier_four(puzzle_netlist):
    solution = sequence_solution()
    solution["hint"] = "look at the reset protocol"
    hints = puzzle.generate_hints(puzzle_netlist, solution)
    assert [h["tier"] for h in hints] == [0, 1, 2, 3, 4]
    assert hints[-1]["text"] == "look at the reset protocol"


def test_generate_hints_has_no_tier_four_when_unauthored(puzzle_netlist):
    hints = puzzle.generate_hints(puzzle_netlist, sequence_solution())
    assert 4 not in [h["tier"] for h in hints]
