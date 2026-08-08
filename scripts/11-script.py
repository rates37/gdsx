from gdsx import config, loader, netlist, analyse

nl = netlist.build(loader.load("samples/sample.gds", config.load()))
regs = [r for r in analyse.find_registers(nl) if r.width > 1]
res = analyse.split_datapath(nl, regs, "S", {"en": 1, "rst_n": 1})
if res is None:
    print("no bus found")
else:
    bus, adder, cmp = res
    print("bus", bus.width, bus.description)
    for i, (n, inv) in enumerate(zip(bus.nets, bus.inverted)):
        print(f"  bit{i}: {n}{' (inverted)' if inv else ''}")
    print("adder gates", len(adder))
    print("comparator gates", len(cmp), sorted(cmp))
