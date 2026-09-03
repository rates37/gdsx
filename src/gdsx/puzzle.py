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

`solution.json` schema
----------------------

`schema_version` is 3. Versions 1 and 2 are still read: every version-3
addition is optional, and a file that uses none of them means exactly what it
meant before. A file declaring a *higher* version is refused rather than
guessed at.

    schema_version  3
    answer_kind     "sequence" | "constant" | "parameter" | "model"

    driver          how to clock the design:
      clock         {"port": ...}
      reset         {"port", "active_low", "protocol": [{"cycles", "values"}]}
      static_inputs {port: value} held for the whole run
      input_cycles  length of the stimulus window (default: the longest track)
      run_cycles    cycles to run when there is no stimulus at all
      input_port    v1: the one serial input port, driven from `key.bits`

    key             the intended stimulus, where the puzzle has one:
      port, bits    v1: one serial track, one character ('0'/'1') per cycle
      tracks        v3: several tracks driven in parallel, each either
                    {"port": "X", "bits": "0101"}  -- one bit per cycle, or
                    {"bus": ["I[1]", "I[0]"],      -- MSB first, and
                     "values": [0, 3, 2]}             one integer per cycle

    answer          v3, `parameter`: {"fields": [{"name", "value", "check"}]}
                    where `check` is one of
                    {"type": "bus_at_cycle", "bus": [...], "cycle": n}
                    {"type": "bus_equals_when", "bus": [...], "when": {...}}
                    {"type": "declared", "reason": "..."}  -- not simulated

    model           v3, `model`: {"watch": [net or instance names, ...]}

    verify          {"method", "predicate"}, the predicate being one of
                    port_reaches_value_by_cycle  (`sequence`, `model`)
                    bus_equals_when              (`constant`)

Cycle numbering is the pack's: cycle 0 is the first cycle after the reset
protocol ends, and every cycle index in a predicate or a field check uses that
origin.

What changed in version 3, and why
----------------------------------

- **Stimulus is a list of tracks**, not a single serial port. Puzzles with two
  data inputs, or with a command word split over `mode`/`addr`/`go`/`D`, could
  not be expressed at all before; `input_port` was the only lever and it was
  one bit wide.
- **A track may be bus-valued** -- `I[1:0]`, `mode[3:0]`, `D[7:0]` -- carrying
  one integer per cycle rather than one bit, MSB first, the same bit order the
  `bus_equals_when` predicate already used for observation.
- **`answer_kind` "parameter"**: an answer that is several named fields, each
  exact-compared. The design need not have a `success` pin, so nothing in this
  path assumes one exists.
- **`answer_kind` "model"**: the answer is a behavioural model, checked
  differentially against the compiled gate tape.
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
from .sim import Simulator, compile as compile_tape, diff_models

SCHEMA_VERSION = 3

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
    a `parameter` puzzle has no success pin at all, and there is nothing to
    point `fanin` at."""
    success_port = solution.get("verify", {}).get("predicate", {}).get("port")
    if success_port is None:
        return None
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
    """One thing `verify` looked at.

    `simulated` is False for a check that passed on the author's word rather
    than on evidence -- a `parameter` field the design cannot be run far
    enough to confirm, such as an LFSR state at cycle 10^12. Those still
    count as `ok`, because refusing to bake a puzzle whose answer is a
    closed-form extrapolation would be refusing the genre; they are reported
    separately so that "verified" never silently means "declared".
    """

    name: str
    ok: bool
    detail: str
    simulated: bool = True


@dataclass
class VerifyResult:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def declared(self) -> list[Check]:
        """Checks that passed without being simulated"""
        return [c for c in self.checks if c.ok and not c.simulated]


@dataclass(frozen=True)
class _Track:
    """One input driven per stimulus cycle.

    A one-bit track has a single port and carries 0 or 1; a bus-valued track
    (`I[1:0]`, `mode[3:0]`, `D[7:0]`) has one port per bit, MSB first, and
    carries an integer per cycle. The two are the same object because
    everything downstream -- vector building, the structural port check, the
    search-space count in `stats` -- treats them identically, and a puzzle
    may mix them freely (puzzle 3 drives `mode`, `addr`, `go` and `D`
    together).
    """

    ports: tuple[str, ...]  # MSB first
    values: tuple[int, ...]  # one per stimulus cycle

    @property
    def width(self) -> int:
        return len(self.ports)

    def at(self, cycle: int) -> dict[str, int]:
        """This track's port assignments for one cycle.

        A track shorter than the stimulus window reads 0 past its end, which
        is what the single-track code did with `key.bits` and is what an
        author means by "and then nothing".
        """
        value = self.values[cycle] if cycle < len(self.values) else 0
        return {
            port: (value >> (self.width - 1 - i)) & 1
            for i, port in enumerate(self.ports)
        }


def _track_values(spec: dict, width: int) -> tuple[int, ...]:
    if "bits" in spec:
        if width != 1:
            raise PuzzleError(
                f"track {spec.get('bus') or spec.get('port')!r} is {width} bits "
                f"wide, so it needs 'values' (one integer per cycle), not 'bits'"
            )
        return tuple(int(c) for c in spec["bits"])
    values = tuple(int(v) for v in spec["values"])
    limit = 1 << width
    for cycle, value in enumerate(values):
        if not 0 <= value < limit:
            raise PuzzleError(
                f"track {spec.get('bus') or spec.get('port')!r} is {width} bits "
                f"wide but carries {value} at cycle {cycle}"
            )
    return values


def _tracks(solution: dict) -> list[_Track]:
    """The stimulus `solution.json` declares, as a list of tracks.

    Three shapes are accepted, in this order: `key.tracks` (v3), the v1 pair
    of `driver.input_port` and `key.bits`, and no stimulus at all. The last
    is not an error -- an autonomous design is driven by its clock and its
    reset and nothing else, and its answer is a value it computes rather than
    a stimulus.
    """
    driver = solution["driver"]
    key = solution.get("key") or {}

    specs = key.get("tracks")
    if specs is None:
        port = driver.get("input_port") or key.get("port")
        if port is None:
            return []
        return [_Track((port,), tuple(int(c) for c in key.get("bits", "")))]

    tracks = []
    for spec in specs:
        ports = tuple(spec["bus"]) if "bus" in spec else (spec["port"],)
        if not ports:
            raise PuzzleError("a track must name at least one port")
        tracks.append(_Track(ports, _track_values(spec, len(ports))))

    # Two tracks driving one port, or a track fighting a static input, would
    # silently resolve to whichever the vector dict merged last. Neither is
    # ever intended, and both are easier to diagnose here than as a puzzle
    # that mysteriously does not raise `success`.
    seen: set[str] = set(driver.get("static_inputs", {}))
    for track in tracks:
        for port in track.ports:
            if port in seen:
                raise PuzzleError(f"{port!r} is driven by more than one track")
            seen.add(port)
    return tracks


def _driver_vectors(
    solution: dict,
) -> tuple[list[dict[str, int]], int, int, dict[str, int]]:
    """Every input vector `verify` should step through, in order.

    Returns (vectors, first_input_cycle, by_cycle, quiescent) where
    `first_input_cycle` is the index into `vectors` of the first cycle
    driven by the key rather than the reset protocol, `by_cycle` is the
    deadline from the predicate, and `quiescent` is the vector to repeat if
    the caller wants to run past the end of the key (reset held inactive,
    every track at 0), both counted the same way the reset protocol's
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
    tracks = _tracks(solution)

    vectors: list[dict[str, int]] = []
    for step in reset["protocol"]:
        values = {clock_port: 0, **step["values"]}
        vectors.extend([dict(values)] * step["cycles"])
    first_input_cycle = len(vectors)

    idle = {clock_port: 0, reset_port: inactive, **static_inputs}
    quiescent = {**idle, **{port: 0 for track in tracks for port in track.ports}}
    if tracks:
        window = driver.get("input_cycles", max(len(t.values) for t in tracks))
        for k in range(window):
            vector = dict(idle)
            for track in tracks:
                vector.update(track.at(k))
            vectors.append(vector)
    else:
        vectors.extend([dict(quiescent)] * driver.get("run_cycles", 0))

    predicate = solution.get("verify", {}).get("predicate", {})
    by_cycle = predicate.get("by_cycle", driver.get("run_cycles", 0))
    return vectors, first_input_cycle, by_cycle, quiescent


def _as_int(value) -> int:
    """A field value, written as a number or as a hex/binary string.

    A 32-bit tap mask is unreadable in decimal, so `parameter` answers are
    normally authored as `"0x8000000b"`; the comparison itself is on the
    integer, so `11`, `"0xb"` and `"0b1011"` are the same answer.
    """
    return value if isinstance(value, int) else int(str(value), 0)


def _show(value: int, authored) -> str:
    """`value` written the way the author wrote the field it came from.

    A tap mask authored as `"0x8000000b"` reads back in hex; a cycle count
    authored as `375` reads back as 375. Reporting a decimal answer in hex is
    technically true and unhelpful.
    """
    return f"{value:#x}" if isinstance(authored, str) else str(value)


def _read_bus(values: dict[str, int], bus: list[str]) -> int:
    got = 0
    for bit in bus:  # MSB first
        got = (got << 1) | (values.get(bit) or 0)
    return got


def _missing_ports(nl, ports) -> list[str]:
    return [p for p in ports if p not in nl.ports]


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

    missing = _missing_ports(nl, [*bus, when["port"]])
    if missing:
        return Check(
            "answer is what the design produces",
            False,
            f"{missing[0]!r} is not a port of the netlist",
        )

    vectors, first_input_cycle, by_cycle, quiescent = _driver_vectors(solution)
    sim = Simulator(nl)
    for cycle, vector in enumerate(vectors):
        values = sim.step(vector)
        if values.get(when["port"]) != when["value"]:
            continue
        got = _read_bus(values, bus)
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


def _check_field(nl, solution: dict, field_spec: dict) -> Check:
    """One field of a `parameter` answer, exact-compared against the design.

    The comparison is deliberately exact and offers no partial credit: for
    the genre this kind exists to serve, a nearly-right polynomial is a wrong
    polynomial.
    """
    name = field_spec["name"]
    label = f"field {name!r}"
    authored = field_spec["value"]
    expected = _as_int(authored)
    check = field_spec.get("check", {"type": "declared"})
    kind = check.get("type")

    if kind == "declared":
        reason = check.get("reason")
        if not reason:
            return Check(
                label,
                False,
                "check type 'declared' needs a 'reason' saying why the field "
                "cannot be simulated",
            )
        return Check(
            label,
            True,
            f"{_show(expected, authored)} taken on the author's word, not "
            f"simulated: {reason}",
            simulated=False,
        )

    if kind not in ("bus_at_cycle", "bus_equals_when"):
        return Check(label, False, f"unknown field check type {kind!r}")

    bus = check["bus"]  # MSB first
    when = check.get("when")
    watched = [*bus, *([when["port"]] if when else [])]
    missing = _missing_ports(nl, watched)
    if missing:
        return Check(label, False, f"{', '.join(missing)} are not ports of the netlist")

    vectors, first_input_cycle, _, quiescent = _driver_vectors(solution)
    if kind == "bus_at_cycle":
        # An absolute cycle can sit past the declared stimulus; idling there
        # is what the player would do too.
        deadline = first_input_cycle + check["cycle"]
        while len(vectors) <= deadline:
            vectors.append(dict(quiescent))

    sim = Simulator(nl)
    for cycle, vector in enumerate(vectors):
        values = sim.step(vector)
        at = cycle - first_input_cycle
        if kind == "bus_at_cycle" and at != check["cycle"]:
            continue
        if when is not None and values.get(when["port"]) != when["value"]:
            continue
        got = _read_bus(values, bus)
        if got != expected:
            return Check(
                label,
                False,
                f"the design reads {_show(got, authored)} at cycle {at}; "
                f"solution.json says the answer is {_show(expected, authored)}",
            )
        return Check(
            label,
            True,
            f"{_show(expected, authored)}, read off the design at cycle {at}",
        )

    return Check(
        label,
        False,
        f"the sampling condition never held in "
        f"{len(vectors) - first_input_cycle} cycles, so the field is never "
        f"observable",
    )


def _check_parameter(nl, solution: dict) -> list[Check]:
    """Every field of the answer, checked independently.

    There is no `success` pin to lean on here and none is looked for: a
    `parameter` puzzle asks what the design *is* -- a polynomial, a divider
    ratio, a state some astronomically distant cycle from now -- and an
    autonomous design that answers that question has no lock to raise.
    """
    fields = solution.get("answer", {}).get("fields")
    if not fields:
        return [
            Check(
                "answer has fields",
                False,
                "a 'parameter' answer needs answer.fields, a list of named "
                "values to exact-compare",
            )
        ]
    names = [f["name"] for f in fields]
    if len(set(names)) != len(names):
        return [Check("answer has fields", False, f"duplicate field names in {names}")]
    return [_check_field(nl, solution, f) for f in fields]


def _check_model(nl, solution: dict) -> Check:
    """The differential the player's model will be graded by is sound.

    A `model` puzzle grades a behavioural model against the compiled gate
    tape, so what has to hold at bake time is that the tape is a faithful
    stand-in for the design and that every signal the puzzle promises to
    compare on is actually observable on it. This runs the tape against the
    netlist simulator -- two independent implementations of the same design,
    already in this library -- over exactly the vectors the puzzle declares.
    A divergence here means the player would be graded against something
    that is not the design.
    """
    watch = solution.get("model", {}).get("watch")
    if not watch:
        return Check(
            "model differential is sound",
            False,
            "a 'model' answer needs model.watch, the signals the player's "
            "model is compared on",
        )

    tape = compile_tape(nl)
    observable = set(tape.names) | set(tape.flop_names)
    unknown = [w for w in watch if w not in observable]
    if unknown:
        return Check(
            "model differential is sound",
            False,
            f"{', '.join(unknown)} cannot be observed on the gate tape, so "
            f"the model differential could never compare them",
        )

    vectors, first_input_cycle, _, _ = _driver_vectors(solution)
    sim = Simulator(nl)

    def netlist_step(vector: dict[str, int]) -> dict[str, int]:
        # Nets and flop state under the same names the tape exposes, so a
        # puzzle may watch a bank's flops and not only its output bus --
        # `success` is 0 on almost every vector, and a model that agrees only
        # on `success` agrees by accident.
        return {**sim.step(vector), **sim.state}

    result = diff_models(tape, netlist_step, vectors, watch=list(watch))
    if not result.agree:
        first = result.first
        return Check(
            "model differential is sound",
            False,
            f"the gate tape and the netlist simulator disagree on "
            f"{first.signal} at cycle {first.cycle - first_input_cycle} "
            f"(tape {first.a}, netlist {first.b}) -- the tape is not a "
            f"faithful stand-in for the design",
        )
    return Check(
        "model differential is sound",
        True,
        f"the gate tape matches the netlist on {len(result.watch)} watched "
        f"signals over {result.cycles_run} cycles",
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

    track_ports = [port for track in _tracks(solution) for port in track.ports]
    for port, direction in (
        (clock_port, "input"),
        (reset_port, "input"),
        *((port, "input") for port in track_ports),
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
    """Check that the puzzle in `puzzle_dir` is solvable as it claims.

    Every answer kind is checked for naming stability -- a fresh extraction
    must name every instance and net exactly as the baked `netlist.json`
    does -- and then for whatever "the intended answer is really the answer"
    means for that kind:

    - `sequence`: the intended stimulus raises `success` on the netlist
      `design.gds` extracts (not on any RTL, there may not be any in the
      bundle), and the constraint structure the author declared -- a
      registered `success`, clocked and reset by the declared ports, driven
      by the declared input tracks -- is what the netlist implements.
    - `constant`: the observation the puzzle nominates really produces the
      declared answer. There is no key to replay, so the check runs the
      other way round.
    - `parameter`: each declared field is exact-compared against the design,
      or, where the design cannot be run far enough, reported as declared
      rather than verified.
    - `model`: the gate tape the player's model will be diffed against
      behaves like the netlist on every watched signal, and the stimulus
      attached as the secondary answer raises `success`.

    Raises `PuzzleError` if `solution.json` is malformed or was written
    against a newer schema than this library understands.
    """
    solution = _read_json(_require(puzzle_dir, SOLUTION))
    version = solution.get("schema_version", 1)
    if version > SCHEMA_VERSION:
        raise PuzzleError(
            f"{puzzle_dir}: {SOLUTION} declares schema_version {version}; this "
            f"library understands up to {SCHEMA_VERSION}"
        )
    gds_path = _require(puzzle_dir, DESIGN_GDS)
    nl = Design.open(gds_path).netlist
    naming = _check_naming(nl, puzzle_dir / NETLIST_JSON)

    kind = solution.get("answer_kind")
    if kind == "sequence":
        return VerifyResult(
            [_check_key(nl, solution), naming, _check_structure(nl, solution)]
        )
    if kind == "constant":
        # No key and no success flop to check the wiring of, so the structural
        # check is the ports the observation depends on -- done inside
        # `_check_constant` before it simulates anything.
        return VerifyResult([_check_constant(nl, solution), naming])
    if kind == "parameter":
        # No success pin exists on this genre, so there is deliberately no
        # structural check here: the fields are the answer, and each one
        # carries its own statement of how it is observed.
        return VerifyResult([*_check_parameter(nl, solution), naming])
    if kind == "model":
        checks = [_check_model(nl, solution), naming]
        # The stimulus is the secondary answer and is optional: a model
        # puzzle whose design has no lock has nothing to raise.
        if solution.get("verify", {}).get("predicate"):
            checks.append(_check_key(nl, solution))
            checks.append(_check_structure(nl, solution))
        return VerifyResult(checks)
    return VerifyResult(
        [
            Check(
                "answer kind supported",
                False,
                f"answer_kind {kind!r} is not implemented yet -- 'sequence', "
                f"'constant', 'parameter' and 'model' are.",
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
        tracks = _tracks(solution) if "driver" in solution else []
        if tracks:
            # The search space is every bit of every track over the window,
            # which for one serial track is exactly the length of `key.bits`.
            window = solution["driver"].get(
                "input_cycles", max(len(t.values) for t in tracks)
            )
            key_bits = sum(t.width * window for t in tracks)
            key_ones = sum(
                bin(v).count("1") for t in tracks for v in t.values[:window]
            )
        success_port = solution.get("verify", {}).get("predicate", {}).get("port")
        if success_port is not None:
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
