"""Record or check the golden tape slices and their truth tables

Usage: uv run python scripts/slice_golden.py [record|check]

One artifact per sample: a set of slices cut from the design's own flops, each
with the full truth table of its target(s). It changes when slicing changes --
a different frontier, a different bit order, an op kept or dropped.

This is the contract between the two evaluators. The notebook's claim
verification evaluates a slice in TypeScript, bit-parallel, up to a million
assignments at a time; `gdsx.sim.slice.run` evaluates the same slice in Python
one assignment at a time. Both must produce this table. `scripts/tape_golden.py`
is the model for this file, including the double run before recording -- a
golden recorded from a non-deterministic source makes every later diff noise.

The slices are chosen by a fixed rule rather than listed by name, because net
names are only stable after the canonical sort and hardcoding one is how these
files rot. The rule picks the narrowest cones (cheap to tabulate in full), the
widest cone under the exhaustive ceiling (the interesting one), and one
multi-target slice over a register group.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gdsx import config, loader, netlist  # noqa: E402
from gdsx.sim import compile  # noqa: E402
from gdsx.sim import slice as sl  # noqa: E402

GOLDEN = ROOT / "tests" / "golden"
SAMPLES = ["sample", "puzzle"]

#: Widest slice we will tabulate in full here. The browser goes to 2**24; a
#: golden file does not need to, and 2**14 rows already exercises every opcode
#: path in a cone many times over.
MAX_WIDTH = 14

#: How many of the narrow cones to keep, so the file does not grow with the
#: design.
NARROW = 6

#: Flops in the multi-target group slice.
GROUP = 4


def _rows(sliced: sl.Slice) -> list[str]:
    """One string of "0"/"1" per assignment, in `targets` order

    Row index `r` is the assignment where bit `i` of `r` is `free[i]`, which is
    the same order the bit-parallel evaluator lays its lanes out in.
    """
    return ["".join(str(v) for v in row) for row in sl.table(sliced)]


def _entry(sliced: sl.Slice) -> dict:
    return {**sliced.to_dict(), "table": _rows(sliced)}


def _chosen(tape) -> list[sl.Slice]:
    by_id = {}
    for name, ident in tape.names.items():
        if ident not in by_id or name < by_id[ident]:
            by_id[ident] = name

    cones = []
    for flop in tape.flops:
        target = by_id.get(flop.d)
        if target is not None:
            cones.append(sl.of(tape, [target]))
    affordable = sorted(
        (s for s in cones if s.n_free <= MAX_WIDTH),
        key=lambda s: (s.n_free, s.target_names),
    )

    chosen = affordable[:NARROW]
    if affordable:
        widest = affordable[-1]
        if widest not in chosen:
            chosen.append(widest)

    group = tape.flop_names[:GROUP]
    if group:
        d_nets = [
            by_id[tape.flops[tape.flop_names.index(f)].d]
            for f in group
            if tape.flops[tape.flop_names.index(f)].d in by_id
        ]
        q_nets = sl.flop_q_nets(tape, group)
        together = sl.of(tape, d_nets, free=q_nets)
        if together.n_free <= MAX_WIDTH:
            chosen.append(together)

    return chosen


def artifacts(sample: str) -> dict[str, str]:
    nl = netlist.build(loader.load(ROOT / "samples" / f"{sample}.gds", config.load()))
    tape = compile(nl)
    document = {
        "tape_version": tape.tape_version,
        "slices": [_entry(s) for s in _chosen(tape)],
    }
    return {f"slice-{sample}": json.dumps(document, sort_keys=True) + "\n"}


def _run_all() -> dict[str, str]:
    results: dict[str, str] = {}
    for sample in SAMPLES:
        results.update(artifacts(sample))
    return results


def _diff(expected: str, actual: str) -> str:
    """Which slice first differs, and in which field"""
    old, new = json.loads(expected), json.loads(actual)
    if old.get("tape_version") != new.get("tape_version"):
        return f"  ~ tape_version: {old.get('tape_version')} -> {new.get('tape_version')}"
    old_slices, new_slices = old.get("slices", []), new.get("slices", [])
    if len(old_slices) != len(new_slices):
        return f"  ~ slices: {len(old_slices)} -> {len(new_slices)}"
    lines = []
    for i, (a, b) in enumerate(zip(old_slices, new_slices)):
        for key in sorted(set(a) | set(b)):
            if a.get(key) != b.get(key):
                lines.append(f"  ~ slices[{i}].{key}: {_where(a.get(key), b.get(key))}")
    return "\n".join(lines) or "  (documents differ but no field does)"


def _where(old, new) -> str:
    if isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            return f"length {len(old)} -> {len(new)}"
        for i, (a, b) in enumerate(zip(old, new)):
            if a != b:
                return f"first difference at index {i}: {a!r} -> {b!r}"
    return f"{old!r} -> {new!r}"


def record() -> int:
    first = _run_all()
    second = _run_all()

    unstable = [name for name in first if first[name] != second[name]]
    if unstable:
        print("NON-DETERMINISTIC SLICING, refusing to record. Differing cases:")
        for name in unstable:
            print(f"\n--- {name} ---\n{_diff(first[name], second[name])}")
        return 1

    GOLDEN.mkdir(parents=True, exist_ok=True)
    for name, text in first.items():
        (GOLDEN / f"{name}.json").write_text(text)
    print(f"recorded {len(first)} golden slice file(s) to {GOLDEN}")
    return 0


def check() -> int:
    current = _run_all()
    failed = []
    for name, text in current.items():
        path = GOLDEN / f"{name}.json"
        if not path.exists():
            failed.append((name, f"no golden file at {path}"))
            continue
        expected = path.read_text()
        if expected != text:
            failed.append((name, _diff(expected, text)))

    if failed:
        print(f"{len(failed)}/{len(current)} golden slice file(s) differ:")
        for name, diff in failed:
            print(f"\n--- {name} ---\n{diff}")
        return 1

    print(f"{len(current)}/{len(current)} golden slice file(s) match")
    return 0


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ("record", "check"):
        print(__doc__)
        return 2
    return {"record": record, "check": check}[sys.argv[1]]()


if __name__ == "__main__":
    raise SystemExit(main())