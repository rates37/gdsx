"""Generate web/src/gdsx-types.ts from the `api` module's return dataclasses.

Usage:
    uv run python scripts/gen_types.py          # write web/src/gdsx-types.ts
    uv run python scripts/gen_types.py check     # exit 1 if the checked-in file is stale

The UI must be typed against the library . This walks the
dataclasses in gdsx.api starting from the payload roots
and emits a TypeScript interface per dataclass it reaches.
"""

from __future__ import annotations

import dataclasses
import sys
import types
import typing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "src" / "gdsx-types.ts"

sys.path.insert(0, str(ROOT / "src"))

from gdsx import api  # noqa: E402

# Root dataclasses exposed across the JSON boundary.
# Every other emitted interface is reached transitively
# by walking these fields.
ROOTS = [api.RefView, api.NetView, api.InstanceView, api.GateView, api.ConeNode]


FIELD_DOCS: dict[tuple[str, str], str] = {
    ("InstanceView", "cell"): 'the FULL sky130 name, e.g. "sky130_fd_sc_hd__dfrtp_2"',
    ("InstanceView", "base_cell"): (
        'what the Liberty library is keyed on, e.g. "dfrtp" -- '
        "a DIFFERENT string from `cell`, confusing them produces "
        '"cell not found" for every instance'
    ),
    ("InstanceView", "connections"): (
        "pin -> net. Pins the layout did not connect are ABSENT, not zero -- "
        "always guard with `pin in connections`"
    ),
    ("ConeNode", "truncated"): (
        "true = there is more below and the walk stopped. Never inferred from "
        "an empty `children` list -- treating a truncated node as a leaf is how "
        "a player wrongly concludes a net is a primary input"
    ),
}

_TS_PRIMITIVES = {str: "string", int: "number", float: "number", bool: "boolean"}


def _is_union(origin: object) -> bool:
    return origin is typing.Union or origin is types.UnionType


def _ts_type(tp: object, seen: dict[str, type]) -> str:
    origin = typing.get_origin(tp)
    if _is_union(origin):
        args = typing.get_args(tp)
        has_none = type(None) in args
        rest = [a for a in args if a is not type(None)]
        rendered = " | ".join(_ts_type(a, seen) for a in rest)
        if len(rest) > 1:
            rendered = f"({rendered})"
        return f"{rendered} | null" if has_none else rendered
    if origin is list:
        (item,) = typing.get_args(tp)
        return f"{_ts_type(item, seen)}[]"
    if origin is dict:
        key, val = typing.get_args(tp)
        return f"Record<{_ts_type(key, seen)}, {_ts_type(val, seen)}>"
    if dataclasses.is_dataclass(tp):
        seen.setdefault(tp.__name__, tp)  # type: ignore[arg-type]
        return tp.__name__  # type: ignore[union-attr]
    if tp in _TS_PRIMITIVES:
        return _TS_PRIMITIVES[tp]
    if tp is type(None):
        return "null"
    raise TypeError(f"gen_types.py does not know how to render {tp!r}")


def _interface(cls: type, seen: dict[str, type]) -> str:
    hints = typing.get_type_hints(cls)
    lines = [f"export interface {cls.__name__} {{"]
    for f in dataclasses.fields(cls):
        ts = _ts_type(hints[f.name], seen)
        doc = FIELD_DOCS.get((cls.__name__, f.name))
        if doc:
            lines.append(f"  /** {doc} */")
        lines.append(f"  {f.name}: {ts};")
    lines.append("}")
    return "\n".join(lines)


def generate() -> str:
    """The .ts source, as a string. Deterministic: interfaces sort by name."""
    seen: dict[str, type] = {cls.__name__: cls for cls in ROOTS}
    queue = list(ROOTS)
    emitted: dict[str, str] = {}
    i = 0
    while i < len(queue):
        cls = queue[i]
        i += 1
        if cls.__name__ in emitted:
            continue
        before = set(seen)
        emitted[cls.__name__] = _interface(cls, seen)
        queue.extend(seen[name] for name in seen.keys() - before)

    header = (
        "// GENERATED FILE -- do not edit by hand.\n"
        "// Run `uv run python scripts/gen_types.py` to regenerate.\n"
        "// Source: src/gdsx/api.py (contract: docs/game/game-plan.md §4b)\n"
    )
    body = "\n\n".join(emitted[name] for name in sorted(emitted))
    return header + "\n" + body + "\n"


def check() -> int:
    """0 if web/src/gdsx-types.ts matches what generate() produces now, else 1."""
    wanted = generate()
    if not OUT.exists():
        print(f"{OUT} does not exist. run `uv run python scripts/gen_types.py`")
        return 1
    if OUT.read_text() != wanted:
        print(f"{OUT} is stale. run `uv run python scripts/gen_types.py`")
        return 1
    return 0


def main(argv: list[str]) -> int:
    if argv[1:2] == ["check"]:
        return check()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(generate())
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
