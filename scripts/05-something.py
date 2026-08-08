"""
Output:

```
reg_dfrtp_2_12 8 None ['dfrtp_2_12', 'dfrtp_2_10', 'dfrtp_2_15', 'dfrtp_2_11', 'dfrtp_2_14', 'dfrtp_2_13', 'dfrtp_2_16', 'dfrtp_2_9']
reg_dfrtp_2_2 8 None ['dfrtp_2_2', 'dfrtp_2_5', 'dfrtp_2_4', 'dfrtp_2_6', 'dfrtp_2_1', 'dfrtp_2_8', 'dfrtp_2_3', 'dfrtp_2_7']
operators ['S = (reg_dfrtp_2_12 + reg_dfrtp_2_2 == 496)']
notes []
```
"""

from gdsx import config, loader, netlist, analyse

nl = netlist.build(loader.load("samples/sample.gds", config.load()))
for r in analyse.find_registers(nl):
    print(r.name, r.width, r.serial_input, r.flops)
a = analyse.analyse(nl)
print("operators", a.operators)
print("notes", a.notes)
