"""Rendering for gdsx.guards: recovered enable conditions"""

from __future__ import annotations

from ..guards import Guards


def report(result: Guards) -> str:
    lines = [
        f"{len(result.candidates)} candidate control nets tested against {len(result.flops)} flops",
        f"{len({g.flop for g in result.guards})} flops have a recovered freeze condition, "
        f"{len(result.ungated)} do not",
        "",
        "GROUPS  (flops that are enabled together)",
    ]
    for key, members in sorted(
        result.groups().items(), key=lambda kv: (-len(kv[1]), kv[0])
    ):
        cond = ", ".join(f"{net}={value}" for net, value in key) or "(never frozen)"
        lines.append(f"  {len(members):3d} flops frozen when {cond}")
        for chunk in range(0, len(members), 6):
            lines.append("        " + ", ".join(members[chunk : chunk + 6]))
    return "\n".join(lines)