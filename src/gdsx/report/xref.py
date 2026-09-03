"""Rendering for gdsx.xref: who drives and reads a net"""

from __future__ import annotations

from ..xref import Xref


def report(found: Xref) -> str:
    lines = [f"net {found.net}" + (f"  [{found.port} port]" if found.port else "")]
    if found.multiply_driven:
        lines.append("  ** more than one driver **")
    if found.undriven:
        lines.append("  ** no driver **")
    for ref in found.drivers:
        lines.append(f"  {ref}")
    for ref in found.readers:
        lines.append(f"  {ref}")
    if not found.drivers and not found.readers:
        lines.append("  (nothing connects to it)")
    return "\n".join(lines)
