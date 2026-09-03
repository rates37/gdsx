"""Rendering for gdsx.sequential: what kind of thing each register is"""

from __future__ import annotations

from ..functions import is_sequential
from ..netlist import Netlist
from ..sequential import Role


def report(nl: Netlist, roles: list[Role], runs: list[list[str]]) -> str:
    flops = sum(1 for i in nl.instances if is_sequential(i.cell))
    lines = [f"{len(roles)} registers over {flops} flops", ""]

    by_kind: dict[str, list[Role]] = {}
    for role in roles:
        by_kind.setdefault(role.kind, []).append(role)
    for kind, found in sorted(by_kind.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"  {len(found):3d} x {kind}")
        for role in found[:6]:
            arrow = ""
            if role.fed_by:
                arrow = f"  <- {', '.join(role.fed_by[:3])}"
            lines.append(f"        {role.register}{arrow}   [{role.evidence}]")
        if len(found) > 6:
            lines.append(f"        ... and {len(found) - 6} more")

    if runs:
        lines += ["", f"{len(runs)} pipelines:"]
        for run in runs:
            lines.append("  " + " -> ".join(run))

    lines += [
        "",
        "  Structural: what feeds what, and what is in the way. Nothing here has",
        "  been proven, and a register that does something the idiom library has",
        "  no name for is reported by its topology alone.",
    ]
    return "\n".join(lines)
