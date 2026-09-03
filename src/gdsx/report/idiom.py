"""Rendering for gdsx.idiom: matched functions and carry chains"""

from __future__ import annotations

from ..idiom import Chain, Matches, library
from ..netlist import Netlist


def report(nl: Netlist, matches: Matches, chains: list[Chain]) -> str:
    lines = [
        f"{len(matches.found)} nets match a known function "
        f"({matches.considered} cuts examined, {len(library())} idioms in the library)",
        "",
    ]
    for idiom, found in matches.by_idiom().items():
        sample = ", ".join(m.net for m in found[:6])
        lines.append(
            f"  {len(found):4d} x {idiom:22s} {sample}"
            + (" ..." if len(found) > 6 else "")
        )

    if chains:
        lines += ["", f"{len(chains)} carry chains:"]
        for chain in sorted(chains, key=lambda c: -c.width):
            lines.append(
                f"  {chain.width}-bit adder: carries {' -> '.join(chain.carries[:6])}"
                + (" ..." if chain.width > 6 else "")
            )
    lines += [
        "",
        "  Matched by canonical function, so a resynthesised adder still matches.",
        "  Nothing here is a claim about what the operands mean.",
    ]
    return "\n".join(lines)
