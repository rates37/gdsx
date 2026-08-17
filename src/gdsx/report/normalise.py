"""Rendering for gdsx.normalise: what a cleanup pass removed"""

from __future__ import annotations

from ..normalise import Normalisation


def report(result: Normalisation) -> str:
    out = [
        f"{result.removed} cells removed, {len(result.merges)} nets merged",
        f"  {len(result.buffers):4d} buffers collapsed",
        f"  {len(result.inverter_pairs):4d} inverter pairs collapsed",
        f"  {len(result.folded):4d} cells folded to a constant",
        f"  {len(result.degenerate):4d} cells degenerated into a wire",
        f"  {len(result.dangling):4d} cells driving nothing",
        f"  {len(result.constants):4d} nets known constant",
    ]
    if result.buffers:
        out.append("\nbuffers")
        for inst, cell, src, dst in result.buffers:
            out.append(f"  {inst:20s} {cell:10s} {dst} := {src}")
    if result.inverter_pairs:
        out.append("\ninverter pairs")
        for inst, cell, src, dst in result.inverter_pairs:
            out.append(f"  {inst:20s} {cell:10s} {dst} := {src}")
    if result.degenerate:
        out.append("\ndegenerate cells")
        for inst, cell, src, dst in result.degenerate:
            out.append(f"  {inst:20s} {cell:10s} {dst} := {src}")
    if result.folded:
        out.append("\nconstant folds")
        for inst, cell, net, value in result.folded:
            out.append(f"  {inst:20s} {cell:10s} {net} = {value}")
    if result.dangling:
        out.append("\ndriving nothing")
        for inst, cell in result.dangling:
            out.append(f"  {inst:20s} {cell}")
    return "\n".join(out)
