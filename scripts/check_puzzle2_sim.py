"""Simulate puzzle 2 three ways -- RTL via iverilog, the netlist extracted
from design.gds, and a software Galois LFSR model -- on the same stimulus,
and diff all three.

docs/game/layout-guide.md §10 point 3 and §8's closing paragraph. The
structural comparison in §8 proves the extracted netlist is the netlist that
went into the placer; this proves that netlist is both the circuit the RTL
describes and the LFSR the puzzle's answer assumes. A disagreement here is a
synthesis or an RTL problem wearing a layout problem's clothes (§12), so it is
worth separating from the layout checks.

Also implements the "4096 simulated cycles match a software Galois LFSR with
mask 0xA3000000 bit for bit" half of puzzle-pack.md §2's bake assertion; the
other half (single chain, three XOR interruptions) is
scripts/check_puzzle2_structure.py.

Usage: uv run python scripts/check_puzzle2_sim.py [runs]
"""

from __future__ import annotations

import random
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from gdsx import config, loader, netlist as N
from gdsx.sim.simulator import Simulator

ROOT = Path(__file__).resolve().parents[1]
PUZZLE_DIR = ROOT / "puzzles" / "2-polynomial"

SAMPLE = re.compile(r"^[01xz]{8}$")
CYCLES = 4096  # matches puzzle-pack.md's bake assertion
MASK = 0xA300_0000
SEED = 0x1357_9BDF

TESTBENCH = """
module tb;
    reg clk = 0, rst_n = 0, enable = 0;
    reg [1:0] sel = 0;
    wire [7:0] O;
    integer i;

    polynomial dut(.clk(clk), .rst_n(rst_n), .enable(enable), .sel(sel), .O(O));

    initial begin
        $readmemb("{stim}", stim);
        for (i = 0; i < {cycles}; i = i + 1) begin
            rst_n  = stim[i][1];
            enable = stim[i][0];
            sel    = i[1:0];
            #1 clk = 1;
            #1 clk = 0;
            $display("%b", O);
        end
        $finish;
    end
    reg [2:0] stim [0:{last}];
endmodule
"""


def _software_lfsr(steps: int) -> list[int]:
    """`states[0]` is the seed; `states[k]` is the state after `k` posedges
    with enable held high (k >= 1)."""
    s = SEED
    states = [s]
    for _ in range(steps):
        s = (s >> 1) ^ (MASK if s & 1 else 0)
        states.append(s)
    return states


def _run_rtl(stimulus: list[tuple[int, int]], workdir: Path) -> list[str]:
    """The RTL's O after each clock edge, via iverilog. `sel` cycles 0..3
    with the loop index so every byte is sampled repeatedly."""
    stim = workdir / "stim.txt"
    # stim bits are [rst_n, enable]; the testbench's `stim` reg is 3 bits
    # wide because $readmemb needs a fixed width -- pad with an unused msb.
    stim.write_text("".join(f"0{r}{e}\n" for r, e in stimulus))
    tb = workdir / "tb.v"
    tb.write_text(
        TESTBENCH.format(
            stim=stim,
            cycles=len(stimulus),
            last=len(stimulus) - 1,
        )
    )
    exe = workdir / "a.out"
    subprocess.run(
        ["iverilog", "-o", str(exe), str(tb), str(PUZZLE_DIR / "polynomial.v")],
        check=True,
        capture_output=True,
    )
    out = subprocess.run(["vvp", str(exe)], check=True, capture_output=True, text=True)
    return [
        line.strip()
        for line in out.stdout.splitlines()
        if SAMPLE.match(line.strip())
    ]


def _run_extracted(sim: Simulator, stimulus: list[tuple[int, int]]) -> list[str]:
    """The same, from the netlist extracted out of the built GDS."""
    sim.reset()
    lines = []
    for i, (rst_n, enable) in enumerate(stimulus):
        sel = i & 0b11
        values = sim.step(
            {
                "clk": 1,
                "rst_n": rst_n,
                "enable": enable,
                "sel[1]": (sel >> 1) & 1,
                "sel[0]": sel & 1,
            }
        )
        bits = "".join(str(values[f"O[{i}]"]) for i in range(7, -1, -1))
        lines.append(bits)
    return lines


def main() -> int:
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    design = loader.load(PUZZLE_DIR / "design.gds", config.load())
    extracted = N.build(design)
    missing = [n for n in (f"O[{i}]" for i in range(8)) if n not in extracted.nets]
    if missing:
        print(f"extracted netlist has no {missing}")
        return 1
    sim = Simulator(extracted)

    rng = random.Random(20250821)
    workdir = Path(tempfile.mkdtemp(prefix="polynomial_sim_"))
    failures = 0

    # Run 0 is the intended stimulus: reset, then free-run with enable held
    # high forever -- the sequence the answer's state values assume.
    cases = [[(0, 1), (0, 1)] + [(1, 1)] * (CYCLES - 2)]
    for _ in range(runs):
        cases.append(
            [(0, 1), (0, 1)]
            + [(rng.randint(0, 1), rng.randint(0, 1)) for _ in range(CYCLES - 2)]
        )

    for index, stimulus in enumerate(cases):
        rtl = _run_rtl(stimulus, workdir)
        got = _run_extracted(sim, stimulus)
        if rtl != got:
            failures += 1
            first = next(i for i, (a, b) in enumerate(zip(rtl, got)) if a != b)
            label = "intended (enable held high)" if index == 0 else f"random stimulus {index}"
            print(f"{label}: RTL vs extracted differ at cycle {first}: "
                  f"RTL {rtl[first]!r} vs extracted {got[first]!r}")

    # The intended run additionally has to match the software Galois model
    # bit for bit -- this is the other half of puzzle-pack.md's bake
    # assertion. cases[0] holds rst_n low for indices 0-1 (the state is the
    # raw seed throughout, no step applied) and then free-runs from index 2
    # on, applying one LFSR step per cycle -- so RTL index i>=2 carries the
    # state after (i-1) steps.
    intended_rtl = _run_rtl(cases[0], workdir)
    states = _software_lfsr(CYCLES)  # states[k] = state after k steps
    model_mismatches = 0
    for i, line in enumerate(intended_rtl):
        state = states[0] if i < 2 else states[i - 1]
        k = i & 0b11
        byte = int(line, 2)
        want = (state >> (8 * k)) & 0xFF
        if byte != want:
            model_mismatches += 1
            if model_mismatches == 1:
                print(f"model mismatch at cycle {i}, sel={k}: "
                      f"RTL byte {byte:#04x}, software model {want:#04x}")
    if model_mismatches:
        print(f"{model_mismatches} byte mismatches against the software Galois model")
        failures += 1

    total = len(cases)
    if failures:
        print(f"{failures}/{total + 1} checks failed")
        return 1
    print(f"RTL and extracted netlist agree on all {total} stimuli "
          f"({CYCLES} cycles each): the intended key plus {runs} random")
    print(f"the intended run also matches a software Galois LFSR (mask "
          f"{MASK:#010x}) bit for bit over {CYCLES} cycles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())