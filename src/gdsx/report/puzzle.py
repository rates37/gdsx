"""Rendering for gdsx.puzzle: bake, verify, stats"""

from __future__ import annotations

from rich.console import Console

from ..puzzle import BakeResult, StatsResult, VerifyResult


def render_bake(console: Console, result: BakeResult) -> None:
    console.print(
        f"[green]baked[/] {result.puzzle_dir} -- "
        f"{result.instances} instances, {result.nets} nets"
    )
    console.print(f"  naming digest {result.naming_hash[:16]}")
    console.print(f"  {result.hint_tiers} hint tiers")
    if result.bundle_path is not None:
        size = result.bundle_path.stat().st_size
        console.print(f"  wrote {result.bundle_path} ({size:,} bytes)")


def render_verify(console: Console, result: VerifyResult) -> bool:
    for check in result.checks:
        tag = "[green]ok[/]" if check.ok else "[red]FAIL[/]"
        console.print(f"{tag}  {check.name}")
        console.print(f"     {check.detail}")
    if result.ok:
        console.print("[green]all checks passed[/]")
    else:
        console.print("[red]verify failed[/]")
    return result.ok


def render_stats(console: Console, result: StatsResult) -> None:
    console.print(
        f"{result.instances} instances "
        f"({result.sequential} sequential, {result.combinational} combinational, "
        f"{result.unrecognised} unrecognised)"
    )
    console.print(
        f"{result.nets} nets, {result.input_ports} input ports, "
        f"{result.output_ports} output ports"
    )
    console.print()
    console.print("cell mix:")
    for cell, count in sorted(
        result.cell_histogram.items(), key=lambda kv: (-kv[1], kv[0])
    ):
        console.print(f"  {count:4d}  {cell}")
    if result.key_bits is not None:
        console.print()
        console.print(
            f"key: {result.key_bits} bits, {result.key_ones} set "
            f"(2^{result.key_bits} search space if brute-forced)"
        )
    if result.d_cone_leaves is not None:
        console.print(f"success's D-cone: {result.d_cone_leaves} leaves")
