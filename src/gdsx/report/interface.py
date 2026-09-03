"""Rendering for gdsx.interface: what each pin is for"""

from __future__ import annotations

from ..interface import Interface


def report(interface: Interface) -> str:
    lines = ["inputs", ""]
    for port in interface.inputs:
        lines.append(f"  {port}")
    lines += ["", "outputs", ""]
    for port in interface.outputs:
        latency = "" if port.latency is None else f"  [{port.latency} cycle latency]"
        lines.append(f"  {port.name:14s} {port.kind}{latency}")
    lines += [
        "",
        "  Clock and reset are structural; the rest is measured by driving the",
        "  design. A port called 'data' is one that changes the state and cannot",
        "  be shown to freeze it, and 'static' means an output that did not move",
        "  in the window -- a UART's byte needs a whole frame, so widen it.",
    ]
    return "\n".join(lines)
