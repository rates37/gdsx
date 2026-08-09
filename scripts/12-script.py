from gdsx import liberty

lib = liberty.library()
bad = []
for n, c in sorted(lib.items()):
    try:
        for e in c.functions.values():
            liberty.to_verilog(e)
        if c.sequential:
            assert (
                c.sequential.clocked_on is not None
                and c.sequential.next_state is not None
            ), "no clk/next"
    except Exception as ex:
        bad.append((n, ex))
print("parsed", len(lib), "bad", bad)
multi = [n for n, c in lib.items() if len(c.outputs) > 1]
print("multi-output:", multi)
nofunc = [n for n, c in lib.items() if not c.has_behaviour]
print("no behaviour:", nofunc)
c = lib["sdfxtp"]
print("sdfxtp next:", liberty.to_verilog(c.sequential.next_state))
c = lib["dfbbn"]
print(
    "dfbbn outs:",
    c.outputs,
    {p: liberty.to_verilog(e) for p, e in c.functions.items()},
    "clr",
    liberty.to_verilog(c.sequential.clear),
    "pre",
    liberty.to_verilog(c.sequential.preset),
)
