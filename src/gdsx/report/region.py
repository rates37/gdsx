"""Rendering for gdsx.region: a carved-out physical block"""

from __future__ import annotations

from ..region import Region


def report(region: Region) -> str:
    return "\n".join(
        [
            f"region {region.box}",
            f"  {len(region.inside)} of {region.total_cells} cells "
            f"({len(region.inside) / region.total_cells:.0%})",
            f"  {len(region.inputs)} inputs, {len(region.outputs)} outputs, "
            f"{len(region.internal)} internal nets",
            f"  cut ratio {region.cut_ratio:.2f} -- {region.verdict}",
        ]
    )
