"""
Output:

```
reg_en 1 en ['dfrtp_2_1']
reg_en 1 en ['dfrtp_2_10']
reg_en 1 en ['dfrtp_2_11']
reg_en 1 en ['dfrtp_2_13']
reg_en 1 en ['dfrtp_2_14']
reg_en 1 en ['dfrtp_2_15']
reg_en 1 en ['dfrtp_2_16']
reg_en 1 en ['dfrtp_2_3']
reg_en 1 en ['dfrtp_2_4']
reg_en 1 en ['dfrtp_2_5']
reg_en 1 en ['dfrtp_2_6']
reg_en 1 en ['dfrtp_2_7']
reg_en 1 en ['dfrtp_2_8']
reg_en 1 en ['dfrtp_2_9']
operators []
notes ['S is never asserted']
```
"""

from gdsx import config, loader, netlist, analyse

nl = netlist.build(loader.load("samples/sample.gds", config.load()))
for r in analyse.find_registers(nl):
    print(r.name, r.width, r.serial_input, r.flops)
a = analyse.analyse(nl)
print("operators", a.operators)
print("notes", a.notes)
