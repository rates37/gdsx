"""Assemble a `.gdsxpuzzle` bundle: one directory per puzzle, zipped.

Usage: uv run python scripts/build_puzzle.py <puzzle-id>

Currently supports only the original puzzle, built from the original challenge
and the artifacts already baked from it (`samples/puzzle.netlist.json`,
`samples/puzzle.render.bin`). The gate tape is compiled fresh here, since no
baked copy exists yet.

Bundle layout (one directory, then zipped to `<id>.gdsxpuzzle`):

    manifest.json   id, title, difficulty, blurb, author, schema_version,
                    tools_enabled[], par_times, tags
    design.gds      the layout itself
    netlist.json    precomputed extraction
    render.bin      self-contained die-view bundle (JSON header + binary blob)
    tape.bin        self-contained compiled gate tape (JSON header + op stream),
                    packed the same way as render.bin for the same reason: the
                    browser needs one fetch, not two files it must not desync.
    solution.json   the key plus the verifier spec

`hints.json` and `notes.md` are not produced here — nothing upstream has baked
them yet, so a bundle claiming to have them would be lying about what's inside.
"""

from __future__ import annotations

import json
import shutil
import struct
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gdsx import config, loader, netlist  # noqa: E402
from gdsx.sim import compile as compile_tape  # noqa: E402

SCHEMA_VERSION = 1

# The 121-bit key, written MSB-first, one bit per rising clk starting the
# cycle after rst_n deasserts. Verified against the extracted netlist, not
# against the RTL.
PUZZLE_KEY = "0000000101010000100000000000010101010000000000001010000001000001000000100000101000010000000100000010000010010001010000000"


def pack_bundle(header: dict, blob: bytes) -> bytes:
    """uint32 length + UTF-8 JSON header + binary blob, self-contained.

    The same framing `RenderBundle.pack` uses, so the browser side only has
    to learn this convention once.
    """
    header_bytes = json.dumps(header).encode("utf-8")
    return struct.pack("<I", len(header_bytes)) + header_bytes + blob


def build_tape_bin(gds_path: Path) -> bytes:
    nl = netlist.build(loader.load(gds_path, config.load()))
    tape = compile_tape(nl)
    header = {
        "tape_version": tape.tape_version,
        "n_nets": tape.n_nets,
        "n_flops": tape.n_flops,
        "n_ops": tape.n_ops,
        "inputs": list(tape.inputs),
        "flops": [
            {
                "d": f.d,
                "q": f.q,
                "clk": f.clk,
                "rst": f.rst,
                "set": f.set,
                "kind": f.kind,
            }
            for f in tape.flops
        ],
        "consts": [list(pair) for pair in tape.consts],
        "names": dict(tape.names),
        "flop_names": list(tape.flop_names),
    }
    return pack_bundle(header, tape.to_bytes())


def build_manifest() -> dict:
    return {
        "id": "original-puzzle",
        "title": "Original Puzzle",
        "schema_version": SCHEMA_VERSION,
        "difficulty": "hard",
        "blurb": (
            "A 728-instance sky130 design with one data input, one clock, "
            "one reset and a success flag. Find the sequence that raises it."
        ),
        "author": "gdsx",
        "tools_enabled": [
            "cone",
            "guards",
            "registers",
            "fsm",
            "sensitivity",
        ],
        "par_times": {"minutes": 120},
        "tags": ["sequence", "warm-up-hard", "sky130"],
    }


def build_solution() -> dict:
    """The key plus a generic verifier spec.

    A driver spec (port, cycles, reset protocol) and a success predicate,
    so the game engine does not need to special-case this puzzle to check it.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "answer_kind": "sequence",
        "key": {
            "port": "I",
            "bits": PUZZLE_KEY,
        },
        "driver": {
            "clock": {"port": "clk"},
            "reset": {
                "port": "rst_n",
                "active_low": True,
                "protocol": [
                    {"cycles": 1, "values": {"rst_n": 0, "enable": 1, "I": 0}},
                ],
                "note": (
                    "One cycle with rst_n low clears the design's async "
                    "resets, including the async-set flops that power up at "
                    "1. Every subsequent cycle runs with rst_n high."
                ),
            },
            "static_inputs": {"enable": 1},
            "input_port": "I",
            "start_cycle": 1,
            "input_cycles": 121,
        },
        "verify": {
            "method": "simulate",
            "predicate": {
                "type": "port_reaches_value_by_cycle",
                "port": "success",
                "value": 1,
                "by_cycle": 121,
                "sticky": True,
            },
        },
        "reveal": {
            "description": (
                "Once success is high, O[7:0] streams an ASCII message, "
                "one byte per cycle, bit i of the byte on O[i]."
            ),
            "port": "O",
            "width": 8,
            "condition": "success == 1",
            "encoding": "ascii_lsb_first",
        },
    }


def build(puzzle_id: str, out_dir: Path) -> Path:
    if puzzle_id != "original-puzzle":
        raise SystemExit(f"no build recipe for puzzle id {puzzle_id!r}")

    gds_path = ROOT / "samples" / "puzzle.gds"
    netlist_path = ROOT / "samples" / "puzzle.netlist.json"
    render_path = ROOT / "samples" / "puzzle.render.bin"
    for p in (gds_path, netlist_path, render_path):
        if not p.exists():
            raise SystemExit(f"missing prerequisite artifact: {p}")

    stage = out_dir / puzzle_id
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    (stage / "manifest.json").write_text(json.dumps(build_manifest(), indent=2) + "\n")
    shutil.copyfile(gds_path, stage / "design.gds")
    shutil.copyfile(netlist_path, stage / "netlist.json")
    shutil.copyfile(render_path, stage / "render.bin")
    (stage / "tape.bin").write_bytes(build_tape_bin(gds_path))
    (stage / "solution.json").write_text(json.dumps(build_solution(), indent=2) + "\n")

    zip_path = out_dir / f"{puzzle_id}.gdsxpuzzle"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(stage.iterdir()):
            zf.write(f, arcname=f.name)

    return zip_path


def main() -> int:
    puzzle_id = sys.argv[1] if len(sys.argv) > 1 else "original-puzzle"
    out_dir = ROOT / "puzzles"
    out_dir.mkdir(exist_ok=True)
    zip_path = build(puzzle_id, out_dir)
    size = zip_path.stat().st_size
    print(f"{zip_path}: {size:,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
