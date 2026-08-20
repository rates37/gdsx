"""Author-time tooling for `.gdsxpuzzle` bundles: bake, verify, stats.

A puzzle bundle is one directory (optionally zipped to `<id>.gdsxpuzzle`):

    manifest.json   id, title, difficulty, ...            (authored by hand)
    design.gds      the layout                            (authored by hand)
    solution.json   the intended key and verifier spec     (authored by hand)
    netlist.json    precomputed extraction                 (written by `bake`)
    render.bin      die-view bundle                        (written by `bake`)
    tape.bin        compiled gate tape                     (written by `bake`)
    hints.json      graded hint tree, tiers 0-4             (written by `bake`)

Everything here works from `design.gds` and the two authored JSON files; none
of it shells out to another tool. Building a puzzle from RTL (`gdsx puzzle
build`) is a separate, much heavier flow and is out of scope for this module.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import guards as guards_mod
from . import netlist as netlist_mod
from .analysis.registers import find_registers
from .core.context import Design
from .core.graph import Graph
from .functions import async_nets, clock_nets, lookup
from .render import RenderBundle
from .render import build as build_render
from .sim import Simulator, compile as compile_tape

MANIFEST = "manifest.json"
DESIGN_GDS = "design.gds"
SOLUTION = "solution.json"
NETLIST_JSON = "netlist.json"
RENDER_BIN = "render.bin"
TAPE_BIN = "tape.bin"
HINTS_JSON = "hints.json"


class PuzzleError(RuntimeError):
    """A puzzle directory is missing a file `bake`/`verify`/`stats` needs"""


def _require(puzzle_dir: Path, name: str) -> Path:
    path = puzzle_dir / name
    if not path.exists():
        raise PuzzleError(f"{puzzle_dir}: missing {name}")
    return path


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text())


#! hints


def _tier0_hint(nl) -> str:
    """`inspect`-level: instance and sequential-cell counts, nothing else"""
    sequential = sum(
        1
        for inst in nl.instances
        if (cell := lookup(inst.cell)) is not None and cell.is_sequential
    )
    return f"{len(nl.instances)} instances, {sequential} sequential"


def _tier1_hint(nl) -> str:
    """`guards`-level: how many registers have no recovered freeze condition"""
    result = guards_mod.find(nl)
    ungated = len(result.ungated)
    return (
        f"{ungated} of {len(result.flops)} registers have no recovered "
        f"freeze condition"
    )


def _tier2_hint(nl) -> str:
    """`analyse.find_registers`-level: how the flops group into registers"""
    registers = find_registers(nl)
    if not registers:
        return "the flops do not group into any registers by shared control and data flow"
    widest = max(registers, key=lambda r: r.width)
    return (
        f"the flops form {len(registers)} register groups; the largest is "
        f"{widest.name} ({widest.width} members)"
    )


def _tier3_hint(nl, solution: dict) -> str | None:
    """`xref.fanin`-level: the D cone of whatever net `solution.json` calls
    success. `None` for puzzles that are not the "reach a value" genre --
    there is nothing to point `fanin` at."""
    if solution.get("answer_kind") != "sequence":
        return None
    success_port = solution["verify"]["predicate"]["port"]
    graph = Graph.of(nl)
    ref = graph.driver_of(success_port)
    if ref is None:
        return f"{success_port} has no driver in the netlist"
    if ref.instance not in graph.seq:
        return (
            f"{success_port} is driven combinationally by {ref.instance} "
            f"({ref.cell}), not by a register"
        )
    leaves = len(graph.d_support(ref.instance))
    return (
        f"{success_port} is driven by one flop ({ref.instance}); its D cone "
        f"has {leaves} leaves"
    )


def generate_hints(nl, solution: dict) -> list[dict]:
    """Tiers 0-3, computed from the netlist itself, plus tier 4 if the
    author wrote one into `solution.json`'s optional `hint` field.

    Every generated hint is an analysis the player could run with the CLI or
    API themselves (`inspect`, `guards`, `find_registers`, `fanin`) -- never
    something only visible from the source. That is the whole point: a hint
    that shows something the tools cannot is a spoiler, not a hint.
    """
    tiers = [
        {"tier": 0, "text": _tier0_hint(nl)},
        {"tier": 1, "text": _tier1_hint(nl)},
        {"tier": 2, "text": _tier2_hint(nl)},
    ]
    tier3 = _tier3_hint(nl, solution)
    if tier3 is not None:
        tiers.append({"tier": 3, "text": tier3})
    authored = solution.get("hint")
    if authored:
        tiers.append({"tier": 4, "text": authored})
    return tiers


#! bake


@dataclass
class BakeResult:
    puzzle_dir: Path
    bundle_path: Path | None
    instances: int
    nets: int
    naming_hash: str
    hint_tiers: int


def _tape_bundle(nl) -> bytes:
    tape = compile_tape(nl)
    header = tape.to_dict()
    del header["ops"]  # goes in the blob instead, not duplicated in JSON
    return RenderBundle(header=header, blob=tape.to_bytes()).pack()


def bake(puzzle_dir: Path, *, zip_bundle: bool = True) -> BakeResult:
    """Regenerate `netlist.json`, `render.bin` and `tape.bin` from `design.gds`

    Reads `manifest.json` for the puzzle id and, unless `zip_bundle` is
    False, also writes `<id>.gdsxpuzzle` next to the puzzle directory.
    """
    manifest = _read_json(_require(puzzle_dir, MANIFEST))
    gds_path = _require(puzzle_dir, DESIGN_GDS)
    solution = _read_json(_require(puzzle_dir, SOLUTION))

    design = Design.open(gds_path)
    layout = design.layout
    macros = design.macros()
    conn = netlist_mod.trace_design(layout, macros)
    nl, net_names = netlist_mod.build_with_net_ids(layout, conn, macros)

    (puzzle_dir / NETLIST_JSON).write_text(netlist_mod.to_json(nl))
    (puzzle_dir / RENDER_BIN).write_bytes(build_render(layout, conn, net_names).pack())
    (puzzle_dir / TAPE_BIN).write_bytes(_tape_bundle(nl))
    hints = generate_hints(nl, solution)
    (puzzle_dir / HINTS_JSON).write_text(json.dumps({"tiers": hints}, indent=2) + "\n")

    bundle_path = None
    if zip_bundle:
        puzzle_id = manifest["id"]
        bundle_path = puzzle_dir.parent / f"{puzzle_id}.gdsxpuzzle"
        with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(puzzle_dir.iterdir()):
                if f.is_file():
                    zf.write(f, arcname=f.name)

    return BakeResult(
        puzzle_dir=puzzle_dir,
        bundle_path=bundle_path,
        instances=len(nl.instances),
        nets=len(nl.nets),
        naming_hash=netlist_mod.naming_digest(nl),
        hint_tiers=len(hints),
    )


#! verify


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class VerifyResult:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)


def _driver_vectors(
    solution: dict,
) -> tuple[list[dict[str, int]], int, int, dict[str, int]]:
    """Every input vector `verify` should step through, in order.

    Returns (vectors, first_input_cycle, by_cycle, quiescent) where
    `first_input_cycle` is the index into `vectors` of the first cycle
    driven by the key rather than the reset protocol, `by_cycle` is the
    deadline from the predicate, and `quiescent` is the vector to repeat if
    the caller wants to run past the end of the key (reset held inactive,
    the input port at 0), both counted the same way the reset protocol's
    `cycles` are: one entry per `Simulator.step` call.

    The reset protocol only specifies the asserting cycle(s); once it ends,
    the reset port is held at its inactive level for the rest of the run,
    same as any other static input. solution.json does not repeat that
    fact per cycle, so it is derived here from `active_low`.
    """
    driver = solution["driver"]
    clock_port = driver["clock"]["port"]
    reset = driver["reset"]
    reset_port = reset["port"]
    inactive = 1 if reset.get("active_low", False) else 0
    static_inputs = driver.get("static_inputs", {})
    # A puzzle need not have a data input at all: an autonomous design is
    # driven by its clock and its reset and nothing else, and its answer is a
    # value it computes rather than a stimulus (game-plan.md §6b).
    input_port = driver.get("input_port")

    vectors: list[dict[str, int]] = []
    for step in reset["protocol"]:
        values = {clock_port: 0, **step["values"]}
        vectors.extend([dict(values)] * step["cycles"])
    first_input_cycle = len(vectors)

    quiescent = {clock_port: 0, reset_port: inactive, **static_inputs}
    if input_port is not None:
        quiescent[input_port] = 0
        bits = [int(c) for c in solution["key"]["bits"]]
        for k in range(driver["input_cycles"]):
            bit = bits[k] if k < len(bits) else 0
            vectors.append(
                {clock_port: 0, reset_port: inactive, **static_inputs, input_port: bit}
            )
    else:
        vectors.extend([dict(quiescent)] * driver.get("run_cycles", 0))

    predicate = solution["verify"]["predicate"]
    by_cycle = predicate.get("by_cycle", driver.get("run_cycles", 0))
    return vectors, first_input_cycle, by_cycle, quiescent


def _check_key(nl, solution: dict) -> Check:
    predicate = solution["verify"]["predicate"]
    if predicate["type"] != "port_reaches_value_by_cycle":
        return Check(
            "key raises success",
            False,
            f"unknown predicate type {predicate['type']!r}",
        )
    port = predicate["port"]
    target = predicate["value"]
    by_cycle = predicate["by_cycle"]
    sticky = predicate.get("sticky", False)

    vectors, first_input_cycle, _, quiescent = _driver_vectors(solution)
    # Run a little past the deadline
    tail = 20
    while len(vectors) < first_input_cycle + by_cycle + tail:
        vectors.append(dict(quiescent))

    sim = Simulator(nl)
    reached_at: int | None = None
    for cycle, vector in enumerate(vectors):
        values = sim.step(vector)
        value = values.get(port)
        if value is None:
            return Check(
                "key raises success", False, f"{port!r} is not a net in the netlist"
            )
        if value == target and reached_at is None:
            reached_at = cycle - first_input_cycle
        if sticky and reached_at is not None and value != target:
            return Check(
                "key raises success",
                False,
                f"{port} reached {target} at input cycle {reached_at} but "
                f"dropped again at cycle {cycle - first_input_cycle} -- "
                f"solution.json claims sticky=true",
            )

    if reached_at is None or reached_at > by_cycle:
        got = "never" if reached_at is None else f"at cycle {reached_at}"
        return Check(
            "key raises success",
            False,
            f"{port} reached {target} {got}; solution.json promises by cycle {by_cycle}",
        )
    return Check(
        "key raises success",
        True,
        f"{port} reached {target} at input cycle {reached_at} (deadline {by_cycle}), "
        f"held for {tail} extra cycles"
        if sticky
        else f"{port} reached {target} at cycle {reached_at}",
    )


def _check_constant(nl, solution: dict) -> Check:
    """The value `solution.json` calls the answer is the value the design
    actually produces.

    For a `constant` puzzle there is no key to replay, so what has to be
    checked is the other direction: drive the design exactly as the puzzle
    tells the player to, sample the observation bus at the moment the puzzle
    nominates, and confirm the answer really is what comes out. Without this
    the bundle could ship an answer nobody can reach.
    """
    predicate = solution["verify"]["predicate"]
    if predicate["type"] != "bus_equals_when":
        return Check(
            "answer is what the design produces",
            False,
            f"unknown predicate type {predicate['type']!r}",
        )
    bus = predicate["bus"]  # MSB first
    expected = predicate["value"]
    when = predicate["when"]

    for port in [*bus, when["port"]]:
        if port not in nl.ports:
            return Check(
                "answer is what the design produces",
                False,
                f"{port!r} is not a port of the netlist",
            )

    vectors, first_input_cycle, by_cycle, quiescent = _driver_vectors(solution)
    sim = Simulator(nl)
    for cycle, vector in enumerate(vectors):
        values = sim.step(vector)
        if values.get(when["port"]) != when["value"]:
            continue
        got = 0
        for bit in bus:
            got = (got << 1) | (values.get(bit) or 0)
        at = cycle - first_input_cycle
        if got != expected:
            return Check(
                "answer is what the design produces",
                False,
                f"at cycle {at}, {when['port']}={when['value']} and the bus "
                f"reads {got:#x}; solution.json says the answer is "
                f"{expected:#x}",
            )
        return Check(
            "answer is what the design produces",
            True,
            f"bus reads {got:#x} at cycle {at}, when "
            f"{when['port']}={when['value']}",
        )
    return Check(
        "answer is what the design produces",
        False,
        f"{when['port']} never reached {when['value']} in "
        f"{len(vectors) - first_input_cycle} cycles, so the answer is never "
        f"observable",
    )


def _check_naming(nl, netlist_json_path: Path) -> Check:
    fresh_hash = netlist_mod.naming_digest(nl)
    if not netlist_json_path.exists():
        return Check(
            "naming stable",
            False,
            f"no {NETLIST_JSON} to compare against -- run `gdsx puzzle bake` first",
        )
    baked = netlist_mod.Netlist.from_dict(_read_json(netlist_json_path))
    baked_hash = netlist_mod.naming_digest(baked)
    if fresh_hash != baked_hash:
        return Check(
            "naming stable",
            False,
            f"a fresh extraction names things differently than {NETLIST_JSON} "
            f"({fresh_hash[:12]} != {baked_hash[:12]}) -- rerun `gdsx puzzle bake`",
        )
    return Check("naming stable", True, f"digest {fresh_hash[:12]}")


def _check_structure(nl, solution: dict) -> Check:
    """The constraint structure the author intended is what the netlist
    implements: `success` is a registered (sticky-capable) signal, clocked
    and reset by the ports `solution.json` declares.

    This is a connectivity check, not a full equivalence proof: it confirms
    the wiring `verify`'s functional check depends on actually exists,
    rather than the functional check having passed by coincidence (e.g. a
    stuck-at-1 net that happens to read as "success" throughout the run).
    """
    driver = solution["driver"]
    predicate = solution["verify"]["predicate"]
    clock_port = driver["clock"]["port"]
    reset_port = driver["reset"]["port"]
    success_port = predicate["port"]

    for port, direction in (
        (clock_port, "input"),
        (reset_port, "input"),
        (driver["input_port"], "input"),
        (success_port, "output"),
    ):
        got = nl.ports.get(port)
        if got != direction:
            return Check(
                "constraint structure",
                False,
                f"{port!r} is {got!r} in the netlist, solution.json expects {direction!r}",
            )

    graph = Graph.of(nl)
    ref = graph.driver_of(success_port)
    if ref is None or ref.instance not in graph.seq:
        return Check(
            "constraint structure",
            False,
            f"{success_port} is not driven by a sequential cell -- "
            f"solution.json's sticky predicate assumes it is",
        )

    cell = lookup(ref.cell)
    connections = graph.by_name[ref.instance].connections
    clk_nets = clock_nets(cell, connections)
    rst_nets = async_nets(cell, connections)

    def _reaches(seed_nets: set[str], target: str) -> bool:
        for net in seed_nets:
            if net == target or target in graph.support(net):
                return True
        return False

    if not _reaches(clk_nets, clock_port):
        return Check(
            "constraint structure",
            False,
            f"{ref.instance} (driving {success_port}) is not clocked from "
            f"{clock_port!r} as solution.json declares",
        )
    if not rst_nets or not _reaches(rst_nets, reset_port):
        return Check(
            "constraint structure",
            False,
            f"{ref.instance} (driving {success_port}) has no async reset "
            f"path from {reset_port!r} as solution.json declares",
        )
    return Check(
        "constraint structure",
        True,
        f"{success_port} <- {ref.instance} ({ref.cell}), clocked from "
        f"{clock_port} and reset from {reset_port}",
    )


def verify(puzzle_dir: Path) -> VerifyResult:
    """Three checks, each of which can fail independently:

    1. the intended key raises `success` on the netlist `design.gds`
       extracts -- not on any RTL, there may not be any in the bundle;
    2. extraction is naming-stable -- a fresh extraction names every
       instance and net exactly as the baked `netlist.json` does;
    3. the constraint structure the author intended (a registered `success`,
       clocked and reset by the declared ports) is what the netlist actually
       implements.
    """
    solution = _read_json(_require(puzzle_dir, SOLUTION))
    gds_path = _require(puzzle_dir, DESIGN_GDS)
    nl = Design.open(gds_path).netlist

    kind = solution.get("answer_kind")
    if kind == "sequence":
        return VerifyResult(
            [
                _check_key(nl, solution),
                _check_naming(nl, puzzle_dir / NETLIST_JSON),
                _check_structure(nl, solution),
            ]
        )
    if kind == "constant":
        # No key and no success flop to check the wiring of, so the structural
        # check is the ports the observation depends on -- done inside
        # `_check_constant` before it simulates anything.
        return VerifyResult(
            [
                _check_constant(nl, solution),
                _check_naming(nl, puzzle_dir / NETLIST_JSON),
            ]
        )
    return VerifyResult(
        [
            Check(
                "answer kind supported",
                False,
                f"answer_kind {kind!r} is not implemented yet -- 'sequence' "
                f"and 'constant' are. See game-plan.md section 6b.",
            )
        ]
    )


#! stats


@dataclass
class StatsResult:
    instances: int
    sequential: int
    combinational: int
    unrecognised: int
    nets: int
    input_ports: int
    output_ports: int
    cell_histogram: dict[str, int]
    key_bits: int | None
    key_ones: int | None
    d_cone_leaves: int | None  # leaves feeding `success`'s next state


def stats(puzzle_dir: Path) -> StatsResult:
    gds_path = _require(puzzle_dir, DESIGN_GDS)
    nl = Design.open(gds_path).netlist

    sequential = 0
    combinational = 0
    unrecognised = 0
    histogram: dict[str, int] = {}
    for inst in nl.instances:
        cell = lookup(inst.cell)
        histogram[inst.cell] = histogram.get(inst.cell, 0) + 1
        if cell is None:
            unrecognised += 1
        elif cell.is_sequential:
            sequential += 1
        else:
            combinational += 1

    key_bits = key_ones = d_cone_leaves = None
    solution_path = puzzle_dir / SOLUTION
    if solution_path.exists():
        solution = _read_json(solution_path)
        if solution.get("answer_kind") == "sequence":
            bits = solution["key"]["bits"]
            key_bits = len(bits)
            key_ones = bits.count("1")
            success_port = solution["verify"]["predicate"]["port"]
            graph = Graph.of(nl)
            ref = graph.driver_of(success_port)
            if ref is not None and ref.instance in graph.seq:
                d_cone_leaves = len(graph.d_support(ref.instance))

    return StatsResult(
        instances=len(nl.instances),
        sequential=sequential,
        combinational=combinational,
        unrecognised=unrecognised,
        nets=len(nl.nets),
        input_ports=sum(1 for d in nl.ports.values() if d == "input"),
        output_ports=sum(1 for d in nl.ports.values() if d == "output"),
        cell_histogram=histogram,
        key_bits=key_bits,
        key_ones=key_ones,
        d_cone_leaves=d_cone_leaves,
    )
