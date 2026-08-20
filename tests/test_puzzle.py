from __future__ import annotations

import json
from pathlib import Path

import pytest

from gdsx import puzzle
from gdsx.core.netlist import Instance, Netlist

PUZZLES = Path(__file__).resolve().parents[1] / "puzzles"


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


#! the two shipped puzzles, which are the regression tests for the answer
#! kinds that already worked


@pytest.mark.parametrize("name", ["original-puzzle", "1-warm-start"])
def test_a_shipped_puzzle_still_verifies(name):
    result = puzzle.verify(PUZZLES / name)
    assert result.ok, [c for c in result.checks if not c.ok]
    assert result.declared == []


def test_the_shipped_sequence_puzzle_runs_the_same_three_checks():
    result = puzzle.verify(PUZZLES / "original-puzzle")
    assert [c.name for c in result.checks] == [
        "key raises success",
        "naming stable",
        "constraint structure",
    ]


def test_the_shipped_constant_puzzle_runs_the_same_two_checks():
    result = puzzle.verify(PUZZLES / "1-warm-start")
    assert [c.name for c in result.checks] == [
        "answer is what the design produces",
        "naming stable",
    ]


#! stimulus: tracks


def tracks_netlist() -> Netlist:
    """Three flops, each capturing one input on every edge.

    `O[1:0]` follows the two-bit bus `I[1:0]` one cycle later, and `success`
    follows the one-bit `go`. Small enough that the intended answer is
    obvious by hand, which is the point -- the tests below are about the
    stimulus and the checks, not about the design.
    """
    nl = Netlist(top="tracks", power_nets={"VGND", "VPWR"})
    nl.instances = [
        Instance(
            name,
            "sky130_fd_sc_hd__dfrtp_2",
            {"CLK": "clk", "D": d, "RESET_B": "rst_n", "Q": q},
        )
        for name, d, q in (
            ("hi", "I[1]", "O[1]"),
            ("lo", "I[0]", "O[0]"),
            ("flag", "go", "success"),
        )
    ]
    for inst in nl.instances:
        for pin, net in inst.connections.items():
            nl.nets.setdefault(net, []).append(f"{inst.name}/{pin}")
    nl.ports = {
        "clk": "input",
        "rst_n": "input",
        "I[1]": "input",
        "I[0]": "input",
        "go": "input",
        "O[1]": "output",
        "O[0]": "output",
        "success": "output",
    }
    return nl


def tracks_solution(**overrides) -> dict:
    """A four-cycle stimulus on two tracks: the bus `I[1:0]` counts 0, 3, 2,
    1 and `go` pulses on the last cycle."""
    solution = {
        "schema_version": 3,
        "answer_kind": "sequence",
        "key": {
            "tracks": [
                {"bus": ["I[1]", "I[0]"], "values": [0, 3, 2, 1]},
                {"port": "go", "bits": "0001"},
            ]
        },
        "driver": {
            "clock": {"port": "clk"},
            "reset": {
                "port": "rst_n",
                "active_low": True,
                "protocol": [{"cycles": 1, "values": {"rst_n": 0}}],
            },
            "input_cycles": 4,
        },
        "verify": {
            "method": "simulate",
            "predicate": {
                "type": "port_reaches_value_by_cycle",
                "port": "success",
                "value": 1,
                "by_cycle": 3,
            },
        },
    }
    solution.update(overrides)
    return solution


def test_a_bus_track_drives_one_port_per_bit_msb_first():
    vectors, first, _, _ = puzzle._driver_vectors(tracks_solution())
    stimulus = vectors[first:]
    got = [(v["I[1]"], v["I[0]"]) for v in stimulus]
    assert got == [(0, 0), (1, 1), (1, 0), (0, 1)]


def test_several_tracks_are_driven_in_the_same_cycle():
    vectors, first, _, _ = puzzle._driver_vectors(tracks_solution())
    stimulus = vectors[first:]
    assert [v["go"] for v in stimulus] == [0, 0, 0, 1]
    driven = {"clk", "rst_n", "I[1]", "I[0]", "go"}
    assert all(driven <= set(v) for v in stimulus)


def test_a_track_shorter_than_the_window_reads_zero_past_its_end():
    solution = tracks_solution()
    solution["driver"]["input_cycles"] = 6
    vectors, first, _, _ = puzzle._driver_vectors(solution)
    assert [v["go"] for v in vectors[first:]] == [0, 0, 0, 1, 0, 0]


def test_the_window_defaults_to_the_longest_track():
    solution = tracks_solution()
    del solution["driver"]["input_cycles"]
    vectors, first, _, _ = puzzle._driver_vectors(solution)
    assert len(vectors) - first == 4


def test_the_quiescent_vector_holds_every_track_at_zero():
    _, _, _, quiescent = puzzle._driver_vectors(tracks_solution())
    assert quiescent == {"clk": 0, "rst_n": 1, "I[1]": 0, "I[0]": 0, "go": 0}


def test_a_single_serial_input_port_still_means_what_it_did():
    """The v1 shape: `driver.input_port` plus `key.bits`, one bit a cycle."""
    solution = {
        "answer_kind": "sequence",
        "key": {"port": "go", "bits": "1011"},
        "driver": {
            "clock": {"port": "clk"},
            "reset": {"port": "rst_n", "active_low": True, "protocol": []},
            "input_port": "go",
            "input_cycles": 4,
        },
        "verify": {"predicate": {}},
    }
    vectors, first, _, _ = puzzle._driver_vectors(solution)
    assert [v["go"] for v in vectors[first:]] == [1, 0, 1, 1]


def test_a_value_too_wide_for_its_bus_is_refused():
    solution = tracks_solution()
    solution["key"]["tracks"][0]["values"] = [0, 4, 0, 0]
    with pytest.raises(puzzle.PuzzleError, match="2 bits wide but carries 4"):
        puzzle._driver_vectors(solution)


def test_two_tracks_on_one_port_are_refused():
    solution = tracks_solution()
    solution["key"]["tracks"].append({"port": "go", "bits": "1000"})
    with pytest.raises(puzzle.PuzzleError, match="driven by more than one track"):
        puzzle._driver_vectors(solution)


def test_a_track_fighting_a_static_input_is_refused():
    solution = tracks_solution()
    solution["driver"]["static_inputs"] = {"go": 1}
    with pytest.raises(puzzle.PuzzleError, match="driven by more than one track"):
        puzzle._driver_vectors(solution)


def test_multi_track_stimulus_raises_success():
    check = puzzle._check_key(tracks_netlist(), tracks_solution())
    assert check.ok, check.detail
    assert "at cycle 3" in check.detail


def test_the_structural_check_covers_every_track_port():
    nl = tracks_netlist()
    nl.ports["I[0]"] = "output"
    check = puzzle._check_structure(nl, tracks_solution())
    assert not check.ok
    assert "'I[0]' is 'output'" in check.detail


#! answer_kind "parameter"


def parameter_solution(*fields) -> dict:
    solution = tracks_solution()
    solution["answer_kind"] = "parameter"
    solution["answer"] = {"fields": list(fields)}
    del solution["verify"]
    return solution


BUS = ["O[1]", "O[0]"]


def test_a_field_is_read_off_the_design_at_a_named_cycle():
    field = {
        "name": "third_symbol",
        "value": 2,
        "check": {"type": "bus_at_cycle", "bus": BUS, "cycle": 2},
    }
    checks = puzzle._check_parameter(tracks_netlist(), parameter_solution(field))
    assert [c.name for c in checks] == ["field 'third_symbol'"]
    assert checks[0].ok, checks[0].detail
    assert checks[0].simulated


def test_a_wrong_field_value_fails_with_what_the_design_reads():
    field = {
        "name": "third_symbol",
        "value": 3,
        "check": {"type": "bus_at_cycle", "bus": BUS, "cycle": 2},
    }
    check = puzzle._check_parameter(tracks_netlist(), parameter_solution(field))[0]
    assert not check.ok
    assert "reads 2 at cycle 2" in check.detail


def test_a_field_may_be_sampled_on_a_condition():
    field = {
        "name": "symbol_at_go",
        "value": 1,
        "check": {
            "type": "bus_equals_when",
            "bus": BUS,
            "when": {"port": "success", "value": 1},
        },
    }
    check = puzzle._check_parameter(tracks_netlist(), parameter_solution(field))[0]
    assert check.ok, check.detail


def test_a_field_is_hex_or_decimal_alike():
    field = {
        "name": "third_symbol",
        "value": "0x2",
        "check": {"type": "bus_at_cycle", "bus": BUS, "cycle": 2},
    }
    assert puzzle._check_parameter(tracks_netlist(), parameter_solution(field))[0].ok


def test_a_field_the_design_cannot_reach_is_declared_not_verified():
    field = {
        "name": "state_at_cycle_1e12",
        "value": "0xdeadbeef",
        "check": {"type": "declared", "reason": "10^12 cycles is not simulable"},
    }
    check = puzzle._check_parameter(tracks_netlist(), parameter_solution(field))[0]
    assert check.ok
    assert not check.simulated
    assert "not simulated" in check.detail


def test_a_declared_field_needs_a_reason():
    field = {"name": "taps", "value": 1, "check": {"type": "declared"}}
    check = puzzle._check_parameter(tracks_netlist(), parameter_solution(field))[0]
    assert not check.ok


def test_several_fields_are_checked_independently():
    good = {
        "name": "third_symbol",
        "value": 2,
        "check": {"type": "bus_at_cycle", "bus": BUS, "cycle": 2},
    }
    bad = {
        "name": "fourth_symbol",
        "value": 0,
        "check": {"type": "bus_at_cycle", "bus": BUS, "cycle": 3},
    }
    checks = puzzle._check_parameter(tracks_netlist(), parameter_solution(good, bad))
    assert [c.ok for c in checks] == [True, False]


def test_a_field_naming_a_port_the_design_does_not_have_fails():
    field = {
        "name": "third_symbol",
        "value": 2,
        "check": {"type": "bus_at_cycle", "bus": ["O[3]", "O[2]"], "cycle": 2},
    }
    check = puzzle._check_parameter(tracks_netlist(), parameter_solution(field))[0]
    assert not check.ok
    assert "O[3], O[2] are not ports" in check.detail


def test_a_parameter_answer_needs_fields():
    solution = parameter_solution()
    check = puzzle._check_parameter(tracks_netlist(), solution)[0]
    assert not check.ok
    assert "answer.fields" in check.detail


def test_duplicate_field_names_are_refused():
    field = {"name": "taps", "value": 1, "check": {"type": "declared", "reason": "x"}}
    checks = puzzle._check_parameter(tracks_netlist(), parameter_solution(field, field))
    assert [c.ok for c in checks] == [False]


#! answer_kind "model"


def model_solution(watch) -> dict:
    solution = tracks_solution()
    solution["answer_kind"] = "model"
    solution["model"] = {"watch": watch}
    return solution


def test_the_gate_tape_matches_the_netlist_on_the_watched_signals():
    check = puzzle._check_model(tracks_netlist(), model_solution(["success", "O[1]"]))
    assert check.ok, check.detail
    assert "2 watched signals" in check.detail


def test_a_model_puzzle_may_watch_flop_state_as_well_as_nets():
    check = puzzle._check_model(tracks_netlist(), model_solution(["hi", "lo", "flag"]))
    assert check.ok, check.detail


def test_a_signal_the_tape_cannot_observe_is_rejected():
    check = puzzle._check_model(tracks_netlist(), model_solution(["success", "nope"]))
    assert not check.ok
    assert "nope cannot be observed" in check.detail


def test_a_model_answer_needs_a_watch_list():
    check = puzzle._check_model(tracks_netlist(), model_solution([]))
    assert not check.ok
    assert "model.watch" in check.detail


#! dispatch


def test_an_unknown_answer_kind_names_the_four_that_exist(tmp_path):
    (tmp_path / "solution.json").write_text(json.dumps({"answer_kind": "location"}))
    (tmp_path / "design.gds").write_bytes(
        (PUZZLES / "1-warm-start" / "design.gds").read_bytes()
    )
    result = puzzle.verify(tmp_path)
    assert not result.ok
    assert "'parameter' and 'model' are" in result.checks[0].detail


def test_a_solution_from_a_newer_schema_is_refused_rather_than_guessed_at(tmp_path):
    (tmp_path / "solution.json").write_text(
        json.dumps({"schema_version": puzzle.SCHEMA_VERSION + 1})
    )
    with pytest.raises(puzzle.PuzzleError, match="understands up to"):
        puzzle.verify(tmp_path)


def test_a_hex_field_is_reported_in_hex_and_a_decimal_one_in_decimal():
    hexed = {
        "name": "third_symbol",
        "value": "0x3",
        "check": {"type": "bus_at_cycle", "bus": BUS, "cycle": 2},
    }
    check = puzzle._check_parameter(tracks_netlist(), parameter_solution(hexed))[0]
    assert "reads 0x2" in check.detail and "answer is 0x3" in check.detail
