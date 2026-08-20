"""Layout generation: turn a `Netlist` into a `design.gds`.

Not extraction and not analysis -- see `docs/game/layout-guide.md` §7. Added
to the import layering table in `docs/game/agent-guide.md` §6 as
`build/ -> core/, geo/, functions.py, netlist.py, synth.py`. Nothing here may
be imported by `core/`, `extract/` or `analysis/`.
"""