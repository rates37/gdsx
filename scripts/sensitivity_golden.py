"""Record or check the golden sensitivity sweeps

Usage: uv run python scripts/sensitivity_golden.py [record|check]

One artifact per sample: the four measurements the game's Experiment Runner
offers, taken through `gdsx.sim.sensitivity`. It changes when the perturbation
semantics change -- a different reset protocol, a perturbation that leaks into
the next cycle, a different notion of "changed".

This is the contract between the two runners. The panel runs its sweeps on the
gate tape in TypeScript, because a sweep is `cycles ** 2` tape steps and doing
that in Pyodide would turn the most-used measurement in the game into a batch
job; `gdsx.sim.sensitivity` runs the same sweep in Python. Both must produce
this file. `scripts/tape_golden.py` is the model here, including the double run
before recording -- a golden recorded from a non-deterministic source makes
every later diff noise.

Nothing about the parameters is hand-picked per sample: the baseline, the
pulsed port and the window all come from the fixed rules below, because a
golden that names `I` or `rst_n` rots the moment a sample changes.
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gdsx import config, loader, netlist  # noqa: E402
from gdsx.sim import compile as _compile  # noqa: E402
from gdsx.sim import sensitivity  # noqa: E402

GOLDEN = ROOT / "tests" / "golden"
SAMPLES = ["sample", "puzzle"]

#: Short enough to stay quick (a sweep is cycles**2 tape steps), long enough
#: that a design with a write window has room to show it.
CYCLES = 40

#: The gap sweep's fixed first pulse, and the spacings tried after it.
FIRST_PULSE = 5
GAPS = range(1, 9)

#: The key the bit-flip sweep perturbs: a pulse every SPACING cycles. A real
#: player flips their own derived key; a golden needs one that does not depend
#: on having solved the puzzle.
SPACING = 7


def _setup(tape) -> dict:
    """The fixed rules: which port is pulsed, and what the rest are held at.

    The pulsed port is the first input by name; every other input is held high
    and every input is low for the one reset step before cycle 0. For the main
    puzzle that resolves to exactly the protocol the game drives -- `I` pulsed,
    `enable` high, `rst_n` high after a one-cycle low -- without naming any of
    them.
    """
    by_id = {ident: name for name, ident in tape.names.items()}
    ports = sorted(by_id[i] for i in tape.inputs)
    key = ports[0]
    baseline = {port: (0 if port == key else 1) for port in ports}
    reset = {port: 0 for port in ports}
    return {"ports": ports, "key": key, "baseline": baseline, "reset": reset}


def _state(tape, setup: dict, perturbations: dict[int, dict[str, int]]) -> dict[str, int]:
    """Final flop state after one trace, through L33's own probe."""
    result = sensitivity.probe(
        tape,
        setup["baseline"],
        perturbations,
        watch=list(tape.flop_names),
        cycles=CYCLES,
        reset=setup["reset"],
    )
    return dict(result.perturbed)


def _changed(before: dict[str, int], after: dict[str, int]) -> list[str]:
    return [name for name in before if before[name] != after[name]]


def artifacts(sample: str) -> dict[str, str]:
    """Every sweep for one sample, as JSON text"""
    nl = netlist.build(loader.load(ROOT / "samples" / f"{sample}.gds", config.load()))
    tape = _compile(nl)
    setup = _setup(tape)
    key = setup["key"]
    watch = list(tape.flop_names)

    # 1. the single-pulse sweep, which IS sensitivity.map. The self-checks warn
    # by design and are part of the recorded result, so they are captured
    # rather than allowed to print during a test run.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        found = sensitivity.map(
            tape,
            cycles=CYCLES,
            baseline=setup["baseline"],
            perturb={key: 1},
            watch=watch,
            reset=setup["reset"],
        )

    idle = _state(tape, setup, {})

    # 2. the gap sweep: a pair of pulses at every spacing.
    gaps = []
    for gap in GAPS:
        second = FIRST_PULSE + gap
        if second >= CYCLES:
            break
        state = _state(tape, setup, {FIRST_PULSE: {key: 1}, second: {key: 1}})
        gaps.append({"gap": gap, "changed": _changed(idle, state)})

    # 3. bit-flip sensitivity, against a key rather than against idle: every
    # cycle of that key flipped in turn, pulses and gaps alike.
    base_pulses = list(range(0, CYCLES, SPACING))
    base = {c: {key: 1} for c in base_pulses}
    base_state = _state(tape, setup, base)
    flips = []
    for cycle in range(CYCLES):
        flipped = dict(base)
        flipped[cycle] = {key: 0 if cycle in base else 1}
        flips.append(
            {"cycle": cycle, "changed": _changed(base_state, _state(tape, setup, flipped))}
        )

    # 4. the reset-value scan: no perturbation at all, the baseline's own state
    # at every cycle. One trace, sampled, rather than a sweep.
    from gdsx.sim import TapeExecutor  # local: only this sweep needs the raw executor

    executor = TapeExecutor(tape)
    executor.step(setup["reset"])
    scan = []
    for cycle in range(CYCLES):
        vector = dict(setup["baseline"])
        vector.update(base.get(cycle, {}))
        executor.step(vector)
        state = executor.state_by_name()
        scan.append("".join(str(state[name]) for name in watch))

    document = {
        "cycles": CYCLES,
        "first_pulse": FIRST_PULSE,
        "spacing": SPACING,
        "base_pulses": base_pulses,
        "flop_names": watch,
        **setup,
        "map": {
            "hits": {name: list(cycles) for name, cycles in found.hits.items()},
            "by_cycle": {str(c): list(names) for c, names in found.by_cycle.items()},
            "unreactive": list(found.unreactive),
            "silent_cycles": list(found.silent_cycles),
        },
        "gaps": gaps,
        "flips": flips,
        #: flop state after each cycle of the base-pulse run, in flop_names order
        "scan": scan,
    }
    return {f"sensitivity-{sample}": json.dumps(document, sort_keys=True, indent=1) + "\n"}


def _run_all() -> dict[str, str]:
    results: dict[str, str] = {}
    for sample in SAMPLES:
        results.update(artifacts(sample))
    return results


def _diff(expected: str, actual: str) -> str:
    """Where two of these documents first differ, field by field"""
    old, new = json.loads(expected), json.loads(actual)
    lines = []
    for key in sorted(set(old) | set(new)):
        if key not in old:
            lines.append(f"  + {key}")
        elif key not in new:
            lines.append(f"  - {key}")
        elif old[key] != new[key]:
            lines.append(f"  ~ {key}: {_where(old[key], new[key])}")
    return "\n".join(lines)


def _where(old, new) -> str:
    if isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            return f"length {len(old)} -> {len(new)}"
        for i, (a, b) in enumerate(zip(old, new)):
            if a != b:
                return f"first difference at index {i}: {a!r} -> {b!r}"
    if isinstance(old, dict) and isinstance(new, dict):
        for k in sorted(set(old) | set(new)):
            if old.get(k) != new.get(k):
                return f"at {k!r}: {old.get(k)!r} -> {new.get(k)!r}"
    return f"{old!r} -> {new!r}"


def record() -> int:
    first = _run_all()
    second = _run_all()

    unstable = [name for name in first if first[name] != second[name]]
    if unstable:
        print("NON-DETERMINISTIC SWEEP, refusing to record. Differing cases:")
        for name in unstable:
            print(f"\n--- {name} ---\n{_diff(first[name], second[name])}")
        return 1

    GOLDEN.mkdir(parents=True, exist_ok=True)
    for name, text in first.items():
        (GOLDEN / f"{name}.json").write_text(text)
    print(f"recorded {len(first)} golden sensitivity file(s) to {GOLDEN}")
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
        print(f"{len(failed)}/{len(current)} golden sensitivity file(s) differ:")
        for name, diff in failed:
            print(f"\n--- {name} ---\n{diff}")
        return 1

    print(f"{len(current)}/{len(current)} golden sensitivity file(s) match")
    return 0


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ("record", "check"):
        print(__doc__)
        return 2
    return {"record": record, "check": check}[sys.argv[1]]()


if __name__ == "__main__":
    raise SystemExit(main())