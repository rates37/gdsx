"""`sim.observe`: quiescent traces and bus decoding."""

from __future__ import annotations

import pathlib

import pytest

from gdsx.core.netlist import Instance, Netlist
from gdsx.sim import Simulator, observe

ROOT = pathlib.Path(__file__).resolve().parents[1]


def ripple3() -> Netlist:
    nl = Netlist(top="ripple3", power_nets={"VGND", "VPWR"})
    nl.instances = [
        Instance("inv0", "sky130_fd_sc_hd__inv_1", {"A": "b0", "Y": "nb0"}),
        Instance("xor1", "sky130_fd_sc_hd__xor2_1", {"A": "b0", "B": "b1", "X": "nb1"}),
        Instance(
            "and01", "sky130_fd_sc_hd__and2_2", {"A": "b0", "B": "b1", "X": "carry01"}
        ),
        Instance(
            "xor2i", "sky130_fd_sc_hd__xor2_1", {"A": "carry01", "B": "b2", "X": "nb2"}
        ),
        Instance(
            "f0",
            "sky130_fd_sc_hd__dfrtp_2",
            {"CLK": "clk", "D": "nb0", "RESET_B": "rst_n", "Q": "b0"},
        ),
        Instance(
            "f1",
            "sky130_fd_sc_hd__dfrtp_2",
            {"CLK": "clk", "D": "nb1", "RESET_B": "rst_n", "Q": "b1"},
        ),
        Instance(
            "f2",
            "sky130_fd_sc_hd__dfrtp_2",
            {"CLK": "clk", "D": "nb2", "RESET_B": "rst_n", "Q": "b2"},
        ),
    ]
    for inst in nl.instances:
        for pin, net in inst.connections.items():
            nl.nets.setdefault(net, []).append(f"{inst.name}/{pin}")
    nl.ports = {"clk": "input", "rst_n": "input"}
    return nl


def test_quiescent_defaults_stimulus_to_every_input_port_at_zero():
    trace = observe.quiescent(ripple3(), 2, ["b0"])
    # held-reset counter (rst_n defaults to 0) never counts
    assert trace.column("b0") == (0, 0)


def test_quiescent_counts_via_net_names_and_instance_names_alike():
    trace = observe.quiescent(
        ripple3(),
        8,
        ["f0", "f1", "f2", "b0", "b1", "b2"],
        stimulus={"clk": 0, "rst_n": 1},
    )
    assert trace.column("f0") == trace.column("b0") == (1, 0, 1, 0, 1, 0, 1, 0)
    assert trace.column("f1") == trace.column("b1") == (0, 1, 1, 0, 0, 1, 1, 0)
    assert len(trace) == 8


def test_quiescent_applies_reset_once_before_the_recorded_cycles():
    fresh = observe.quiescent(ripple3(), 4, ["b0"], stimulus={"clk": 0, "rst_n": 1})
    reset_first = observe.quiescent(
        ripple3(),
        4,
        ["b0"],
        stimulus={"clk": 0, "rst_n": 1},
        reset={"clk": 0, "rst_n": 0},
    )
    assert fresh.column("b0") == reset_first.column("b0")


def test_bus_decodes_int_hex_and_ascii():
    trace = observe.Trace(
        watch=("O[0]", "O[1]", "O[2]"),
        rows=(
            {"O[0]": 0, "O[1]": 1, "O[2]": 0},  # 0b010 = 2
            {"O[0]": 1, "O[1]": 0, "O[2]": 1},  # 0b101 = 5
        ),
    )
    assert observe.bus(trace, "O", decode="int") == [2, 5]
    assert observe.bus(trace, "O", decode="hex") == ["0x2", "0x5"]
    assert observe.bus(trace, "O", decode="ascii") == [".", "."]


def test_bus_raises_when_the_prefix_matches_nothing():
    trace = observe.Trace(watch=("x",), rows=({"x": 0},))
    with pytest.raises(ValueError):
        observe.bus(trace, "O")


def test_bus_rejects_an_unknown_decode():
    trace = observe.Trace(watch=("O[0]",), rows=({"O[0]": 1},))
    with pytest.raises(ValueError):
        observe.bus(trace, "O", decode="bogus")


#! puzzle reproductions


@pytest.mark.slow
def test_reproduces_log_cycles(puzzle_netlist):
    clow = ["dfrtp_2_40", "dfrtp_2_41", "dfrtp_2_47", "dfrtp_2_51"]
    watch = clow + ["dfrtp_2_61"]
    trace = observe.quiescent(
        puzzle_netlist,
        125,
        watch,
        stimulus={"clk": 0, "rst_n": 1, "enable": 1, "I": 0},
        reset={"clk": 0, "rst_n": 0, "enable": 1, "I": 0},
    )

    lines = [" cyc | low[40,41,47,51] | end | en | lowne0"]
    for k, row in enumerate(trace.rows):
        q40, q41, q47, q51, q61 = (row[name] for name in watch)
        low = f"{q40}{q41}{q47}{q51}"
        end = q40 & q51 & (1 - q41) & (1 - q47)
        en = 1 - q61
        lowne0 = 1 if (q47 | q40 | q41 | q51) else 0
        lines.append(f"{k:4d} | {low}             |  {end}  | {en}  | {lowne0}")
    got = "\n".join(lines) + "\n"

    expected = (ROOT / "work" / "log-cycles.txt").read_text()
    assert got == expected


@pytest.mark.slow
def test_reproduces_the_two_stars_message(puzzle_netlist):
    key = "0000000101010000100000000000010101010000000000001010000001000001000000100000101000010000000100000010000010010001010000000"
    bits = [int(c) for c in key]

    watch = ["success"] + [f"O[{i}]" for i in range(8)] + ["dfrtp_2_50", "dfrtp_2_18"]
    simulator = Simulator(puzzle_netlist)
    simulator.step({"clk": 0, "rst_n": 0, "enable": 1, "I": 0})

    rows = []
    for k in range(len(bits) + 80):
        b = bits[k] if k < len(bits) else 0
        settled = simulator.step({"clk": 0, "rst_n": 1, "enable": 1, "I": b})
        rows.append(
            {
                name: settled[name] if name in settled else simulator.state[name]
                for name in watch
            }
        )
    trace = observe.Trace(watch=tuple(watch), rows=tuple(rows))

    first_success = next(k for k, row in enumerate(trace.rows) if row["success"])
    assert first_success == 121
    assert trace.rows[-1]["dfrtp_2_50"] == 0
    assert trace.rows[-1]["dfrtp_2_18"] == 0

    chars = observe.bus(trace, "O", decode="ascii")
    message = "".join(ch for row, ch in zip(trace.rows, chars) if row["success"])
    assert message == "(* TWO STARS *)" + "." * (len(trace) - first_success - 15)
