"""Record or check golden CLI outputs. Usage: uv run python scripts/golden.py [record|check]"""

from __future__ import annotations

import difflib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden"

CASES = [
    # (name, argv)
    ("sample-inspect", ["inspect", "samples/sample.gds"]),
    ("sample-pins", ["pins", "samples/sample.gds"]),
    ("sample-extract", ["extract", "samples/sample.gds", "-o", "{tmp}"]),
    ("sample-normalise", ["normalise", "samples/sample.gds"]),
    ("sample-placement", ["placement", "samples/sample.gds"]),
    ("sample-guards", ["guards", "samples/sample.gds"]),
    ("sample-ports", ["ports", "samples/sample.gds"]),
    ("sample-registers", ["registers", "samples/sample.gds"]),
    ("sample-analyse", ["analyse", "samples/sample.gds"]),
    ("sample-fsm", ["fsm", "samples/sample.gds"]),
    ("puzzle-inspect", ["inspect", "samples/puzzle.gds"]),
    ("puzzle-extract", ["extract", "samples/puzzle.gds", "-o", "{tmp}"]),
    ("puzzle-normalise", ["normalise", "samples/puzzle.gds"]),
    ("puzzle-placement", ["placement", "samples/puzzle.gds"]),
    ("puzzle-guards", ["guards", "samples/puzzle.gds"]),
    ("puzzle-ports", ["ports", "samples/puzzle.gds"]),
    ("puzzle-registers", ["registers", "samples/puzzle.gds"]),
    ("puzzle-analyse", ["analyse", "samples/puzzle.gds"]),
    ("puzzle-fsm", ["fsm", "samples/puzzle.gds"]),
]

_TIME_RE = re.compile(r"\d+\.\d+s")


def _normalise(text: str, run_dir: Path) -> str:
    """Strip absolute paths and timings so goldens are machine-independent.

    `run_dir` is the per-case scratch cwd (see `_run_case`); it stands in for
    the repo root here because commands are deliberately run outside it, to
    avoid ever writing into the tracked `out/` directory.
    """
    text = text.replace(str(run_dir), "<ROOT>")
    text = text.replace(str(ROOT), "<ROOT>")
    return _TIME_RE.sub("<TIME>", text)


def _run_case(name: str, template: list[str], tmp_base: Path) -> str:
    """Run one CLI case in an isolated cwd; return its normalised golden text.

    The cwd is isolated (not the repo root) so that commands which write
    files using a relative default (e.g. `normalise` writes to `./out`) never
    touch the repo's tracked `out/` directory. `uv run --project` lets `uv`
    find the project regardless of cwd.
    """
    run_dir = tmp_base / name
    run_dir.mkdir(parents=True)
    is_file_case = "{tmp}" in template

    argv = []
    for tok in template:
        if tok == "{tmp}":
            argv.append(str(run_dir / "out"))
        elif tok.startswith("samples/"):
            argv.append(str(ROOT / tok))
        else:
            argv.append(tok)

    env = dict(os.environ, COLUMNS="100", NO_COLOR="1")
    proc = subprocess.run(
        ["uv", "run", "--project", str(ROOT), "gdsx", *argv],
        cwd=run_dir,
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"case {name!r} failed (exit {proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
        )

    if is_file_case:
        # Golden the written file contents, not the console output.
        out_dir = run_dir / "out"
        files = sorted(out_dir.iterdir(), key=lambda p: p.name)
        if not files:
            raise RuntimeError(f"case {name!r} wrote no files")
        parts = [f"=== {f.name} ===\n{f.read_text()}" for f in files]
        return _normalise("\n".join(parts), run_dir)

    return _normalise(proc.stdout + proc.stderr, run_dir)


def _run_all() -> dict[str, str]:
    results = {}
    with tempfile.TemporaryDirectory(prefix="gdsx-golden-") as tmp_base:
        tmp_base = Path(tmp_base)
        for name, template in CASES:
            results[name] = _run_case(name, template, tmp_base)
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
        print("NON-DETERMINISTIC OUTPUT -- refusing to record. Differing cases:")
        for name in diffs:
            print(f"\n--- {name} ---")
            print(_diff(first[name], second[name], "run1", "run2"))
        return 1

    GOLDEN.mkdir(parents=True, exist_ok=True)
    for name, text in first.items():
        (GOLDEN / f"{name}.txt").write_text(text)
    print(f"recorded {len(first)} golden case(s) to {GOLDEN}")
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
        print(f"{len(failed)}/{len(current)} golden case(s) differ:")
        for name, diff in failed:
            print(f"\n--- {name} ---\n{diff}")
        return 1

    print(f"{len(current)}/{len(current)} golden case(s) match")
    return 0


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ("record", "check"):
        print(__doc__)
        return 2
    return {"record": record, "check": check}[sys.argv[1]]()


if __name__ == "__main__":
    raise SystemExit(main())