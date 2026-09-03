"""

OUutput:

```
dfrtp_2_2 ['B', 'en']
dfrtp_2_10 ['en']
dfrtp_2_12 ['A', 'en']
```
"""

from gdsx import config, loader, netlist
from gdsx.core.graph import Graph
from gdsx.functions import lookup, is_sequential

nl = netlist.build(loader.load("samples/sample.gds", config.load()))
for f in nl.instances:
    if is_sequential(f.cell) and f.name in ("dfrtp_2_12", "dfrtp_2_2", "dfrtp_2_10"):
        fn = lookup(f.cell)
        deps = Graph.of(nl).support(f.connections[fn.data])
        print(f.name, sorted(d for d in deps if d in nl.ports))
