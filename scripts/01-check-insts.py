"""
loads a GDS file, counts the top-cell instances (expanding regular
arrays into their total element count), and reports any text labels found on
each layer.

Running on the warmup problem:

output:

```
268 VIA_L1M1_PR_MR
313 VIA_M1M2_PR
45 VIA_M2M3_PR
5 VIA_M3M4_PR
75 VIA_via2_3_2000_480_1_6_320_320
75 VIA_via3_4_2000_480_1_5_400_400
75 VIA_via4_5_2000_480_1_5_400_400
13 VIA_via5_6_2000_2000_1_1_1600_1600
1 sky130_fd_sc_hd__a21bo_2
1 sky130_fd_sc_hd__a21boi_2
1 sky130_fd_sc_hd__a21o_2
5 sky130_fd_sc_hd__a31o_2
7 sky130_fd_sc_hd__and2_2
1 sky130_fd_sc_hd__and3_2
2 sky130_fd_sc_hd__and4bb_2
3 sky130_fd_sc_hd__clkbuf_16
58 sky130_fd_sc_hd__decap_3
16 sky130_fd_sc_hd__dfrtp_2
16 sky130_fd_sc_hd__mux2_1
4 sky130_fd_sc_hd__nand2_2
8 sky130_fd_sc_hd__nor2_2
1 sky130_fd_sc_hd__o21bai_2
5 sky130_fd_sc_hd__or2_2
93 sky130_fd_sc_hd__tapvpwrvgnd_1
3 sky130_fd_sc_hd__xnor2_2
5 sky130_fd_sc_hd__xor2_2
total 1099
TEXT layer 70 5 6 ['A', 'B', 'S', 'clk', 'en', 'rst_n']
TEXT layer 71 5 2 ['VGND', 'VPWR']
TEXT layer 72 5 2 ['VGND', 'VPWR']
```
"""

import klayout.db as db
from collections import Counter

ly = db.Layout()
ly.read("samples/sample.gds")

top = ly.top_cell()

# Count instances by referenced cell name
# For arrays, count every array element (na * nb)
c = Counter()
for inst in top.each_inst():
    c[ly.cell(inst.cell_index).name] += (
        inst.na * inst.nb if inst.is_regular_array() else 1
    )

for k, v in sorted(c.items()):
    print(v, k)

print("total", sum(c.values()))

# labels at top
n = 0
for lidx in ly.layer_indexes():
    info = ly.get_info(lidx)
    txt = [s for s in top.shapes(lidx).each() if s.is_text()]
    if txt:
        print(
            "TEXT layer",
            info.layer,
            info.datatype,
            len(txt),
            [t.text.string for t in txt[:20]],
        )
