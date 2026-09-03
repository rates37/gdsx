"""Record or check the golden topological order

Usage: uv run python scripts/topo_golden.py [record|check]

`scripts/golden.py` covers the CLI's text output, but no command prints the
order `Graph.topo()` produces, and the simulator's results are identical
whatever that order is (it is a valid order either way).

Two orders per sample:

  topo-<sample>.txt      Graph.topo() with its default sources
  simorder-<sample>.txt  the order Simulator holds in `combinational`,
                         i.e. topo(sources=free nets + power + flop Q)
"""

from __future__ import annotations

import difflib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gdsx import config, loader, netlist  # noqa: E402
from gdsx.core.graph import Graph  # noqa: E402
from gdsx.sim import Simulator  # noqa: E402

GOLDEN = ROOT / "tests" / "golden"
SAMPLES = ["sample", "puzzle"]


def orders(sample: str) -> dict[str, str]:
    """The two topological orders for one sample, as newline-joined names"""
    nl = netlist.build(loader.load(ROOT / "samples" / f"{sample}.gds", config.load()))
    graph = Graph.of(nl)
    sim = Simulator(nl)
    return {
        f"topo-{sample}": "\n".join(i.name for i in graph.topo()) + "\n",
        f"simorder-{sample}": "\n".join(i.name for i, _ in sim.combinational) + "\n",
    }


def _run_all() -> dict[str, str]:
    results: dict[str, str] = {}
    for sample in SAMPLES:
        results.update(orders(sample))
    return results


def _diff(expected: str, actual: str, expected_label: str, actual_label: str) -> str:
    return "".join(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            actual.splitlines(keepends=True),
            expected_label,
            actual_label,
        )
    )


def record() -> int:
    first = _run_all()
    second = _run_all()

    diffs = [name for name in first if first[name] != second[name]]
    if diffs:
        print("NON-DETERMINISTIC ORDER, refusing to record. Differing cases:")
        for name in diffs:
            print(f"\n--- {name} ---")
            print(_diff(first[name], second[name], "run1", "run2"))
        return 1

    GOLDEN.mkdir(parents=True, exist_ok=True)
    for name, text in first.items():
        (GOLDEN / f"{name}.txt").write_text(text)
    print(f"recorded {len(first)} golden order(s) to {GOLDEN}")
    return 0


def check() -> int:
    current = _run_all()
    failed = []
    for name, text in current.items():
        golden_path = GOLDEN / f"{name}.txt"
        if not golden_path.exists():
            failed.append((name, f"no golden file at {golden_path}"))
            continue
        expected = golden_path.read_text()
        if expected != text:
            failed.append((name, _diff(expected, text, "golden", "actual")))

    if failed:
        print(f"{len(failed)}/{len(current)} golden order(s) differ:")
        for name, diff in failed:
            print(f"\n--- {name} ---\n{diff}")
        return 1

    print(f"{len(current)}/{len(current)} golden order(s) match")
    return 0


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ("record", "check"):
        print(__doc__)
        return 2
    return {"record": record, "check": check}[sys.argv[1]]()


if __name__ == "__main__":
    raise SystemExit(main())
