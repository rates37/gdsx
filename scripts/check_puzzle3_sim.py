"""Simulate puzzle 3 two ways -- RTL via iverilog and the netlist extracted
from design.gds -- on the same stimulus, and diff them.

docs/game/layout-guide.md §10 point 3 and §8's closing paragraph. The
structural comparison in §8 proves the extracted netlist is the netlist that
went into the placer; this proves that netlist is the circuit the RTL
describes. A disagreement here is a synthesis or an RTL problem wearing a
layout problem's clothes (§12), so it is worth separating from the layout
checks.

Run 0 is the intended key: arm at cycle 0, idle, privileged write at cycle 4.
The rest are random command traffic, which is the interesting case here --
random `mode`/`addr`/`go` words exercise the arm/settle interlock in ways the
intended key never does, and a resynthesised netlist that got the settle
counter subtly wrong would still pass on the key alone.

Usage: uv run python scripts/check_puzzle3_sim.py [runs]
"""

from __future__ import annotations

import random
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from gdsx import config, loader
from gdsx import netlist as N
from gdsx.sim.simulator import Simulator

ROOT = Path(__file__).resolve().parents[1]
PUZZLE_DIR = ROOT / "puzzles" / "3-gatekeeper"

SAMPLE = re.compile(r"^[01xz]{9}$")
CYCLES = 64

# One stimulus word, msb first: rst_n, enable, mode[3:0], addr[2:0], go, D[7:0]
WORD_BITS = 18

TESTBENCH = """
module tb;
    reg clk = 0;
    wire rst_n  = stim[i][17];
    wire enable = stim[i][16];
    wire [3:0] mode = stim[i][15:12];
    wire [2:0] addr = stim[i][11:9];
    wire go = stim[i][8];
    wire [7:0] D = stim[i][7:0];
    wire [7:0] O;
    wire success;
    integer i;
    reg [{msb}:0] stim [0:{last}];

    gatekeeper dut(.clk(clk), .rst_n(rst_n), .enable(enable), .mode(mode),
                   .addr(addr), .go(go), .D(D), .O(O), .success(success));

    initial begin
        $readmemb("{stim}", stim);
        for (i = 0; i < {cycles}; i = i + 1) begin
            #1 clk = 1;
            #1 clk = 0;
            #1 $display("%b%b", success, O);
        end
        $finish;
    end
endmodule
"""


def _bits(word: int) -> str:
    return format(word, f"0{WORD_BITS}b")


def _word(rst_n: int, enable: int, mode: int, addr: int, go: int, d: int) -> int:
    return (
        (rst_n << 17) | (enable << 16) | (mode << 12) | (addr << 9) | (go << 8) | d
    )


def _run_rtl(stimulus: list[int], workdir: Path) -> list[str]:
    stim = workdir / "stim.txt"
    stim.write_text("".join(f"{_bits(w)}\n" for w in stimulus))
    tb = workdir / "tb.v"
    tb.write_text(
        TESTBENCH.format(
            stim=stim,
            cycles=len(stimulus),
            last=len(stimulus) - 1,
            msb=WORD_BITS - 1,
        )
    )
    exe = workdir / "a.out"
    subprocess.run(
        ["iverilog", "-o", str(exe), str(tb), str(PUZZLE_DIR / "gatekeeper.v")],
        check=True,
        capture_output=True,
    )
    out = subprocess.run(["vvp", str(exe)], check=True, capture_output=True, text=True)
    return [line.strip() for line in out.stdout.splitlines() if SAMPLE.match(line.strip())]


def _run_extracted(sim: Simulator, stimulus: list[int]) -> list[str]:
    sim.reset()
    lines = []
    for word in stimulus:
        vector = {"clk": 1, "rst_n": (word >> 17) & 1, "enable": (word >> 16) & 1,
                  "go": (word >> 8) & 1}
        for b in range(4):
            vector[f"mode[{b}]"] = (word >> (12 + b)) & 1
        for b in range(3):
            vector[f"addr[{b}]"] = (word >> (9 + b)) & 1
        for b in range(8):
            vector[f"D[{b}]"] = (word >> b) & 1
        values = sim.step(vector)
        bits = str(values["success"]) + "".join(
            str(values[f"O[{b}]"]) for b in range(7, -1, -1)
        )
        lines.append(bits)
    return lines


def _intended() -> list[int]:
    """Reset, arm at cycle 0, idle, privileged write at cycle 4."""
    stim = [_word(0, 1, 0, 0, 0, 0)]
    for c in range(CYCLES - 1):
        if c == 0:
            stim.append(_word(1, 1, 0b1011, 6, 1, 0x00))
        elif c == 4:
            stim.append(_word(1, 1, 0b0110, 5, 1, 0x5A))
        else:
            stim.append(_word(1, 1, 0, 0, 0, 0))
    return stim


def _random(rng: random.Random) -> list[int]:
    """Reset, then random command traffic. `mode` is drawn from the three
    recognised words as often as from the other thirteen, so the interlock
    gets exercised rather than the design idling through the whole run."""
    stim = [_word(0, 1, 0, 0, 0, 0)]
    for _ in range(CYCLES - 1):
        mode = rng.choice([0b1001, 0b1011, 0b0110, rng.randrange(16)])
        stim.append(
            _word(
                1,
                rng.choice([1, 1, 1, 0]),
                mode,
                rng.randrange(8),
                rng.choice([1, 1, 0]),
                rng.randrange(256),
            )
        )
    return stim


def main() -> int:
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    design = loader.load(PUZZLE_DIR / "design.gds", config.load())
    extracted = N.build(design)
    watched = [*(f"O[{i}]" for i in range(8)), "success"]
    missing = [n for n in watched if n not in extracted.nets]
    if missing:
        print(f"extracted netlist has no {missing}")
        return 1
    sim = Simulator(extracted)

    rng = random.Random(20260821)
    workdir = Path(tempfile.mkdtemp(prefix="gatekeeper_sim_"))

    cases = [_intended()] + [_random(rng) for _ in range(runs)]
    failures = 0
    for index, stimulus in enumerate(cases):
        rtl = _run_rtl(stimulus, workdir)
        got = _run_extracted(sim, stimulus)
        if rtl != got:
            failures += 1
            first = next(i for i, (a, b) in enumerate(zip(rtl, got)) if a != b)
            label = "intended key" if index == 0 else f"random stimulus {index}"
            print(f"{label}: RTL vs extracted differ at cycle {first}: "
                  f"RTL {rtl[first]!r} vs extracted {got[first]!r}")

    # The intended run must also do what the puzzle claims: success low until
    # cycle 5 (index 6 -- index 0 is the reset cycle), high from then on.
    intended = _run_rtl(cases[0], workdir)
    early = [i - 1 for i, line in enumerate(intended[:6]) if line[0] == "1"]
    late = [i - 1 for i, line in enumerate(intended[6:], 6) if line[0] != "1"]
    if early or late:
        failures += 1
        print(f"intended key: success high early at {early}, low late at {late}")

    if failures:
        print(f"{failures} checks failed")
        return 1
    print(f"RTL and extracted netlist agree on all {len(cases)} stimuli "
          f"({CYCLES} cycles each): the intended key plus {runs} random")
    print("on the intended key, success is low through cycle 4 and high from "
          "cycle 5 onwards")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())