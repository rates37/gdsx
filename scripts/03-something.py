"""
Sample output:

```
flops 16 comb 63
clock sense {'n431': 1, 'n113': 1}
241 255 sum 496 S= 1 lsb-first S= 0
240 255 sum 495 S= 0 lsb-first S= 0
255 241 sum 496 S= 1 lsb-first S= 0
248 248 sum 496 S= 1 lsb-first S= 0
0 0 sum 0 S= 0 lsb-first S= 0
255 255 sum 510 S= 0 lsb-first S= 0
200 40 sum 240 S= 0 lsb-first S= 0
```
"""

from gdsx import config, loader, netlist, sim

nl = netlist.build(loader.load("samples/sample.gds", config.load()))
s = sim.Simulator(nl)
print("flops", len(s.flops), "comb", len(s.combinational))
print("clock sense", s.clock_sense({"clk": 0, "A": 0, "B": 0, "en": 0, "rst_n": 1}))


def shift(a, b, msb_first=True):
    s.reset()
    s.step({"clk": 0, "A": 0, "B": 0, "en": 0, "rst_n": 0})
    order = range(7, -1, -1) if msb_first else range(8)
    for i in order:
        v = s.step(
            {"clk": 0, "A": (a >> i) & 1, "B": (b >> i) & 1, "en": 1, "rst_n": 1}
        )
    return v["S"]


for a, b in [
    (241, 255),
    (240, 255),
    (255, 241),
    (248, 248),
    (0, 0),
    (255, 255),
    (200, 296 & 255),
]:
    print(a, b, "sum", a + b, "S=", shift(a, b), "lsb-first S=", shift(a, b, False))
