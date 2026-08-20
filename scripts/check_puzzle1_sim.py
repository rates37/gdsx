"""Simulate puzzle 1 twice -- from the RTL and from the extracted netlist --
on the same stimulus, and diff.

docs/game/layout-guide.md §10 point 3. The structural comparison in §8 proves
the extracted netlist is the netlist that went in; this proves the netlist
that went in is the circuit the RTL describes. A disagreement here is a
synthesis or an RTL problem wearing a layout problem's clothes (§12), so it
is worth separating from the layout checks.

Usage: uv run python scripts/check_puzzle1_sim.py [runs]
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
PUZZLE_DIR = ROOT / "puzzles" / "1-warm-start"

SAMPLE = re.compile(r"^[01xz]{8} [01xz]$")
CYCLES = 40  # per run; long enough for `ph` to wrap several times
OUTPUTS = [f"O_{i}" for i in range(8)] + ["success"]

TESTBENCH = """
module tb;
    reg clk = 0, rst_n = 0, enable = 0;
    wire [7:0] O;
    wire success;
    integer i;

    warm_start dut(.clk(clk), .rst_n(rst_n), .enable(enable),
                   .O(O), .success(success));

    initial begin
        $readmemb("{stim}", stim);
        for (i = 0; i < {cycles}; i = i + 1) begin
            rst_n  = stim[i][1];
            enable = stim[i][0];
            #1 clk = 1;
            #1 clk = 0;
            $display("%b %b", O, success);
        end
        $finish;
    end
    reg [1:0] stim [0:{last}];
endmodule
"""


def _run_rtl(stimulus: list[tuple[int, int]], workdir: Path) -> list[str]:
    """The RTL's O/success after each clock edge, via iverilog."""
    stim = workdir / "stim.txt"
    stim.write_text("".join(f"{r}{e}\n" for r, e in stimulus))
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
        ["iverilog", "-o", str(exe), str(tb), str(PUZZLE_DIR / "warm_start.v")],
        check=True,
        capture_output=True,
    )
    out = subprocess.run(["vvp", str(exe)], check=True, capture_output=True, text=True)
    # vvp writes `$finish called at ...` to stdout alongside the $display
    # lines, so match the shape we asked for rather than taking every line.
    return [
        line.strip()
        for line in out.stdout.splitlines()
        if SAMPLE.match(line.strip())
    ]


def _run_extracted(sim: Simulator, stimulus: list[tuple[int, int]]) -> list[str]:
    """The same, from the netlist extracted out of the built GDS."""
    sim.reset()
    lines = []
    for rst_n, enable in stimulus:
        values = sim.step({"clk": 1, "rst_n": rst_n, "enable": enable})
        bits = "".join(str(values[f"O_{i}"]) for i in range(7, -1, -1))
        lines.append(f"{bits} {values['success']}")
    return lines


def main() -> int:
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    design = loader.load(PUZZLE_DIR / "design.gds", config.load())
    extracted = N.build(design)
    missing = [n for n in OUTPUTS if n not in extracted.nets]
    if missing:
        print(f"extracted netlist has no {missing}")
        return 1
    sim = Simulator(extracted)

    rng = random.Random(20250820)
    workdir = Path(tempfile.mkdtemp(prefix="warm_start_sim_"))
    failures = 0

    # Run 0 is the intended stimulus -- reset, then run free with `enable`
    # held high, which is the sequence the puzzle's own narrative describes.
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
            label = "intended key" if index == 0 else f"random stimulus {index}"
            print(f"{label}: differ at cycle {first}: RTL {rtl[first]!r} "
                  f"vs extracted {got[first]!r}")

    total = len(cases)
    if failures:
        print(f"{failures}/{total} stimuli disagree")
        return 1
    print(f"RTL and extracted netlist agree on all {total} stimuli "
          f"({CYCLES} cycles each): the intended key plus {runs} random")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())