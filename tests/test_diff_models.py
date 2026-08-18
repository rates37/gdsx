"""`sim.diff_models`: first-divergence comparison between two models.

Uses the same hand-built two-flop netlist as `test_sensitivity.py`: one
flop enabled (`D = I & enA`), one disabled (`D = I & enB` with `enB` held
low) -- so the right answer is obvious by hand.
"""

from __future__ import annotations

import pytest

from gdsx.core.netlist import Instance, Netlist
from gdsx.sim import GateTape, compile as compile_tape, diff_models


def one_flop_netlist() -> Netlist:
    """A single flop, `D = I`, no combinational logic in the way."""
    nl = Netlist(top="one_flop", power_nets={"VGND", "VPWR"})
    nl.instances = [
        Instance(
            "flop",
            "sky130_fd_sc_hd__dfrtp_2",
            {"CLK": "clk", "D": "I", "RESET_B": "rst_n", "Q": "q"},
        ),
    ]
    for inst in nl.instances:
        for pin, net in inst.connections.items():
            nl.nets.setdefault(net, []).append(f"{inst.name}/{pin}")
    nl.ports = {"clk": "input", "rst_n": "input", "I": "input"}
    return nl


@pytest.fixture
def tape() -> GateTape:
    return compile_tape(one_flop_netlist())


VECTORS = [
    {"clk": 0, "rst_n": 1, "I": 1},
    {"clk": 0, "rst_n": 1, "I": 0},
    {"clk": 0, "rst_n": 1, "I": 1},
]


def test_two_identical_tapes_agree(tape):
    got = diff_models(tape, tape, VECTORS)
    assert got.agree
    assert got.first is None
    assert got.cycles_run == len(VECTORS)


def test_tape_against_a_correct_callback_agrees(tape):
    def model(vector: dict[str, int]) -> dict[str, int]:
        # the flop clocks every step, so its state after a step is this
        # step's D -- a correct model of `one_flop_netlist`
        return {"flop": vector["I"]}

    got = diff_models(tape, model, VECTORS, watch=["flop"])
    assert got.agree
    assert got.cycles_run == len(VECTORS)


def test_tape_against_a_wrong_callback_reports_the_first_divergence(tape):
    def stuck_model(vector: dict[str, int]) -> dict[str, int]:
        return {"flop": 0}  # never reacts to I -- wrong from cycle 0

    got = diff_models(tape, stuck_model, VECTORS, watch=["flop"])
    assert not got.agree
    assert got.first.cycle == 0
    assert got.first.signal == "flop"
    assert got.first.a == 1  # the real flop caught I=1
    assert got.first.b == 0
    assert got.cycles_run == 1  # stops at the first mismatch


def test_divergence_is_found_partway_through():
    """A model that is right for a while and then drifts."""
    nl = one_flop_netlist()
    tape = compile_tape(nl)

    def drifting_model(vector: dict[str, int]) -> dict[str, int]:
        drifting_model.calls += 1
        if drifting_model.calls <= 2:
            return {"flop": vector["I"]}
        return {"flop": 1 - vector["I"]}  # wrong from the third vector on

    drifting_model.calls = 0

    got = diff_models(tape, drifting_model, VECTORS, watch=["flop"])
    assert not got.agree
    assert got.first.cycle == 2
    assert got.cycles_run == 3


def test_no_watch_defaults_to_the_shared_signal_names(tape):
    def model(vector: dict[str, int]) -> dict[str, int]:
        return {"flop": vector["I"], "extra_only_the_model_knows": 1}

    got = diff_models(tape, model, VECTORS)
    assert "flop" in got.watch
    assert "extra_only_the_model_knows" not in got.watch  # not shared


def test_watch_naming_an_unobservable_signal_raises(tape):
    def model(vector: dict[str, int]) -> dict[str, int]:
        return {"flop": vector["I"]}

    with pytest.raises(ValueError, match="not_a_real_signal"):
        diff_models(tape, model, VECTORS, watch=["flop", "not_a_real_signal"])


def test_no_shared_signal_names_raises(tape):
    def model(vector: dict[str, int]) -> dict[str, int]:
        return {"totally_different_name": vector["I"]}

    with pytest.raises(ValueError, match="no observable signal"):
        diff_models(tape, model, VECTORS)


def test_two_callbacks_can_be_compared_directly():
    def a(vector: dict[str, int]) -> dict[str, int]:
        return {"x": vector["I"]}

    def b(vector: dict[str, int]) -> dict[str, int]:
        return {"x": vector["I"]}

    got = diff_models(a, b, VECTORS)
    assert got.agree
