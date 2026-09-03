"""Record or check the golden gate tapes and traces

Usage: uv run python scripts/tape_golden.py [record|check]

Two artifacts per sample, and they catch different things:

  tape-<sample>.json    the compiled tape. Changes when the ENCODING changes --
                        a different opcode chosen for a cell, a different net
                        numbering, an op emitted in a different place.
  trace-<sample>.json   200 cycles of committed input vectors, and what the
                        design did. Changes when the BEHAVIOUR changes.

The trace is the contract between the two executors. The TypeScript executor in
`web/src/sim/` is not written yet; when it is, it reads these same files and
must reproduce them byte for byte, which is why the digest is taken over the
named nets in name order -- the one view of the value array both executors can
build without agreeing on the compiler's temporaries.

`scripts/topo_golden.py` is the model for this file, down to the double run
before recording: a golden recorded from a non-deterministic source makes every
later diff noise.
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gdsx import config, loader, netlist  # noqa: E402
from gdsx.sim import TapeExecutor, compile  # noqa: E402

GOLDEN = ROOT / "tests" / "golden"
SAMPLES = ["sample", "puzzle"]
CYCLES = 200
SEED = 20260818


def _digest(values: dict[str, int], order: list[str]) -> str:
    return hashlib.sha256(
        "".join(str(values[net]) for net in order).encode("ascii")
    ).hexdigest()


def artifacts(sample: str) -> dict[str, str]:
    """The tape and the trace for one sample, as JSON text"""
    nl = netlist.build(loader.load(ROOT / "samples" / f"{sample}.gds", config.load()))
    tape = compile(nl)
    executor = TapeExecutor(tape)

    ports = sorted(net for net, kind in nl.ports.items() if kind == "input")
    rng = random.Random(SEED)
    vectors = ["".join(str(rng.randint(0, 1)) for _ in ports) for _ in range(CYCLES)]

    order = sorted(tape.names)
    flops, digests = [], []
    executor.reset()
    for bits in vectors:
        executor.step({port: int(bit) for port, bit in zip(ports, bits)})
        flops.append("".join(str(v) for v in executor.state))
        digests.append(_digest(executor.values_by_name(), order))

    trace = {
        "tape_version": tape.tape_version,
        "cycles": CYCLES,
        "seed": SEED,
        "ports": ports,
        "flop_names": list(tape.flop_names),
        #: one string of "0"/"1" per cycle, in `ports` order
        "vectors": vectors,
        #: flop state after each cycle, in `flop_names` order
        "flops": flops,
        #: sha256 of the value of every NAMED net, in net-name order. Temporary
        #: nets are excluded on purpose: they are a compiler detail, and a tape
        #: that allocates them differently is still the same circuit.
        "nets": digests,
    }
    return {
        f"tape-{sample}": json.dumps(tape.to_dict(), sort_keys=True) + "\n",
        f"trace-{sample}": json.dumps(trace, sort_keys=True) + "\n",
    }


def _run_all() -> dict[str, str]:
    results: dict[str, str] = {}
    for sample in SAMPLES:
        results.update(artifacts(sample))
    return results


def _diff(expected: str, actual: str) -> str:
    """Where two of these JSON documents first differ, field by field

    A unified text diff of a one-line JSON document says nothing useful, so
    this reports the differing key and, for the op stream, the first differing
    op rather than the whole array.
    """
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
    return f"{old!r} -> {new!r}"


def record() -> int:
    first = _run_all()
    second = _run_all()

    unstable = [name for name in first if first[name] != second[name]]
    if unstable:
        print("NON-DETERMINISTIC TAPE, refusing to record. Differing cases:")
        for name in unstable:
            print(f"\n--- {name} ---\n{_diff(first[name], second[name])}")
        return 1

    GOLDEN.mkdir(parents=True, exist_ok=True)
    for name, text in first.items():
        (GOLDEN / f"{name}.json").write_text(text)
    print(f"recorded {len(first)} golden tape/trace file(s) to {GOLDEN}")
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
        print(f"{len(failed)}/{len(current)} golden tape/trace file(s) differ:")
        for name, diff in failed:
            print(f"\n--- {name} ---\n{diff}")
        return 1

    print(f"{len(current)}/{len(current)} golden tape/trace file(s) match")
    return 0


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ("record", "check"):
        print(__doc__)
        return 2
    return {"record": record, "check": check}[sys.argv[1]]()


if __name__ == "__main__":
    raise SystemExit(main())
