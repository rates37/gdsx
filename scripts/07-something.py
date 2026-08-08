"""
Output:

```
reg_A 8 A ['dfrtp_2_12', 'dfrtp_2_10', 'dfrtp_2_15', 'dfrtp_2_11', 'dfrtp_2_14', 'dfrtp_2_13', 'dfrtp_2_16', 'dfrtp_2_9']
reg_B 8 B ['dfrtp_2_2', 'dfrtp_2_5', 'dfrtp_2_4', 'dfrtp_2_6', 'dfrtp_2_1', 'dfrtp_2_8', 'dfrtp_2_3', 'dfrtp_2_7']
['S = (reg_A + reg_B == 496)'] []
```
"""

from gdsx import config, loader, netlist, analyse

nl = netlist.build(loader.load("samples/sample.gds", config.load()))
a = analyse.analyse(nl)
for r in a.registers:
    print(r.name, r.width, r.serial_input, r.flops)
print(a.operators, a.notes)
