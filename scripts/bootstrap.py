"""Rebuild every generated file a fresh clone does not carry.

Usage: uv run python scripts/bootstrap.py [--check]

The repository tracks inputs, not outputs. What is committed is the two
sample layouts, each level's `design.gds`, the RTL and layout specs they were
built from, `puzzles/catalog.json`, and the golden outputs that are a contract
rather than an artifact. Everything else on this list is derived, and deriving
it is this script:

    samples/puzzle.netlist.json     extraction of samples/puzzle.gds
    samples/puzzle.render.bin       its die-view bundle
    samples/puzzle.tape.bin         its compiled gate tape
    samples/sample.tape.bin         the same, for samples/sample.gds
    puzzles/*/manifest.json         `gdsx puzzle sync`, from catalog.json
    puzzles/*/solution.json         the same
    puzzles/*/netlist.json          `gdsx puzzle bake`, from design.gds
    puzzles/*/render.bin            the same
    puzzles/*/tape.bin              the same
    puzzles/*/hints.json            the same
    puzzles/<id>.gdsxpuzzle         the same

Run it once after cloning, before `npm test` or `npm run dev` in `web/` --
both read the puzzle artifacts, and `web/scripts/sync-assets.mjs` refuses to
build without them and names this script when it does.

Order matters in one place: `sync` writes the manifests, and `bake` reads a
manifest to learn the puzzle id it is baking. Sync first.

`--check` rebuilds nothing and reports what is missing, for a quick "is this
checkout usable yet".

**About ten seconds, and it needs only `uv`.** It does not need yosys: the
levels' `design.gds` files are committed rather than re-synthesised, because
they are the one generated thing that does not reproduce -- a different yosys
version picks a different gate mix for the same RTL, which would renumber
every net and invalidate every hint and walkthrough written against them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gdsx import catalog as catalog_mod  # noqa: E402
from gdsx import config, loader, netlist  # noqa: E402
from gdsx import puzzle as puzzle_mod  # noqa: E402

SAMPLES = ROOT / "samples"
PUZZLES = ROOT / "puzzles"

#: Every derived path, for `--check`. Kept beside the code that writes them.
DERIVED_SAMPLES = (
    SAMPLES / "puzzle.netlist.json",
    SAMPLES / "puzzle.render.bin",
    SAMPLES / "puzzle.tape.bin",
    SAMPLES / "sample.tape.bin",
)
DERIVED_PER_PUZZLE = (
    "manifest.json",
    "solution.json",
    "netlist.json",
    "render.bin",
    "tape.bin",
    "hints.json",
)


def puzzle_dirs() -> list[Path]:
    """Every level, in name order -- a directory with a design to bake."""
    return sorted(d for d in PUZZLES.iterdir() if d.is_dir() and (d / "design.gds").is_file())


def missing() -> list[Path]:
    out = [p for p in DERIVED_SAMPLES if not p.exists()]
    for directory in puzzle_dirs():
        out += [directory / name for name in DERIVED_PER_PUZZLE if not (directory / name).exists()]
        entry = PUZZLES / f"{directory.name}.gdsxpuzzle"
        if not entry.exists():
            out.append(entry)
    return out


def bake_samples() -> None:
    """The two sample layouts' artifacts, which the node test suites read.

    `samples/puzzle.netlist.json` is written with `json.dumps`' own defaults
    rather than through `netlist.to_json`, which indents. That is not a
    preference: `web/scripts/measure-m0.mjs` reports this file's size as an M0
    metric, and reformatting it would move that number by 90 KB without a byte
    of the design changing.
    """
    from gdsx import api

    for source in (SAMPLES / "puzzle.gds", SAMPLES / "sample.gds"):
        if not source.exists():
            raise SystemExit(f"missing tracked input: {source}")

    nl = netlist.build(loader.load(SAMPLES / "puzzle.gds", config.load()))
    (SAMPLES / "puzzle.netlist.json").write_text(json.dumps(nl.to_dict()))
    print(f"  samples/puzzle.netlist.json")

    envelope = json.loads(api.open_design((SAMPLES / "puzzle.gds").read_bytes()))
    if not envelope["ok"]:
        raise SystemExit(f"could not open samples/puzzle.gds: {envelope['error']}")
    blob = api.render_bundle(envelope["data"]["handle"])
    (SAMPLES / "puzzle.render.bin").write_bytes(blob)
    print(f"  samples/puzzle.render.bin ({len(blob):,} bytes)")

    (SAMPLES / "puzzle.tape.bin").write_bytes(puzzle_mod._tape_bundle(nl))
    print(f"  samples/puzzle.tape.bin")

    sample_nl = netlist.build(loader.load(SAMPLES / "sample.gds", config.load()))
    (SAMPLES / "sample.tape.bin").write_bytes(puzzle_mod._tape_bundle(sample_nl))
    print(f"  samples/sample.tape.bin")


def main() -> int:
    if "--check" in sys.argv:
        gaps = missing()
        if not gaps:
            print("bootstrap: every generated file is present")
            return 0
        print(f"bootstrap: {len(gaps)} file(s) missing, run `uv run python scripts/bootstrap.py`")
        for path in gaps[:20]:
            print(f"  {path.relative_to(ROOT)}")
        if len(gaps) > 20:
            print(f"  ... and {len(gaps) - 20} more")
        return 1

    print("samples:")
    bake_samples()

    print("\ncatalog -> manifest.json, solution.json:")
    result = catalog_mod.sync(PUZZLES)
    print(f"  {len(result.written)} written, {len(result.checked)} checked")

    print("\nbaking levels:")
    for directory in puzzle_dirs():
        baked = puzzle_mod.bake(directory)
        print(f"  {directory.name}: {baked.instances} instances, {baked.hint_tiers} hint tiers")

    print("\nDone. Next: cd web && npm install && npm test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())