"""Layout generation: turn a `Netlist` into a `design.gds`.

Not extraction and not analysis. It sits in the import layering table as
`build/ -> core/, geo/, functions.py, netlist.py, synth.py`. Nothing here may
be imported by `core/`, `extract/` or `analysis/`.
"""