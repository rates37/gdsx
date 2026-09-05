"""Rendering for gdsx.puzzle: bake, verify, stats -- and gdsx.catalog: sync"""

from __future__ import annotations

from rich.console import Console

from ..catalog import SyncResult
from ..puzzle import BakeResult, StatsResult, VerifyResult


def render_sync(console: Console, result: SyncResult, *, check: bool) -> bool:
    """The catalog sync. Returns False when a check found drift, so the CLI
    can exit non-zero without this function deciding what that means."""
    if result.clean:
        console.print(f"[green]catalog clean[/] -- {len(result.checked)} file(s) up to date")
        return True
    verb = "would rewrite" if check else "wrote"
    colour = "yellow" if check else "green"
    console.print(f"[{colour}]{verb}[/] {len(result.written)} of {len(result.checked)} file(s)")
    for path in result.written:
        console.print(f"  {path}")
    if check:
        console.print("[yellow]run `gdsx puzzle sync` to bring them back in line[/]")
    return not check


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
        if not check.ok:
            tag = "[red]FAIL[/]"
        elif not check.simulated:
            tag = "[yellow]said[/]"
        else:
            tag = "[green]ok[/]"
        console.print(f"{tag}  {check.name}")
        console.print(f"     {check.detail}")
    if result.ok and result.declared:
        console.print(
            f"[yellow]all checks passed, {len(result.declared)} on the "
            f"author's word[/]"
        )
    elif result.ok:
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
