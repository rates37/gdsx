"""
Output:

```
hold en=1, rst_n=1; feed bits MSB first
(241, 255) verified
(242, 254) verified
(243, 253) verified
(244, 252) verified
(245, 251) verified
(246, 250) verified
(247, 249) verified
(248, 248) verified
(249, 247) verified
(250, 246) verified
```
"""

from gdsx import config, loader, netlist, analyse

nl = netlist.build(loader.load("samples/sample.gds", config.load()))
mode, sols = analyse.solve(nl, "S")
print(mode.description if mode else "no mode")
for v, ok in sols:
    print(v, "verified" if ok else "MISMATCH")
