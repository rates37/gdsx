"""
loads a GDS file, looks up a predefined set of cells, and prints
each cell's bounding box, child instance count, and the geometry
and text labels present on non-empty layers

sample output on warmup gds:

```
== sky130_fd_sc_hd__nand2_2 bbox (-190,-240;2490,2960) insts 0
   L67/20: 6 shapes  texts=[]
   L67/44: 10 shapes  texts=[]
   L68/20: 2 shapes  texts=[]
   L68/16: 2 shapes  texts=[]
   L68/5: 2 shapes  texts=['VGND', 'VPWR']
   L64/16: 1 shapes  texts=[]
   L64/5: 1 shapes  texts=['VPB']
   L122/16: 1 shapes  texts=[]
   L236/0: 1 shapes  texts=[]
   L66/20: 2 shapes  texts=[]
   L64/59: 1 shapes  texts=['VNB']
   L81/4: 1 shapes  texts=[]
   L94/20: 1 shapes  texts=[]
   L93/44: 1 shapes  texts=[]
   L65/20: 2 shapes  texts=[]
   L64/20: 1 shapes  texts=[]
   L78/44: 1 shapes  texts=[]
   L95/20: 1 shapes  texts=[]
   L66/44: 22 shapes  texts=[]
   L83/44: 1 shapes  texts=['nand2_2']
   L67/16: 7 shapes  texts=[]
   L67/5: 5 shapes  texts=['Y', 'Y', 'Y', 'A', 'B']
== VIA_M1M2_PR bbox (-160,-160;160,160) insts 0
   L68/20: 1 shapes  texts=[]
   L69/20: 1 shapes  texts=[]
   L68/44: 1 shapes  texts=[]
== sky130_fd_sc_hd__dfrtp_2 bbox (-190,-240;9850,2960) insts 0
   L67/20: 36 shapes  texts=[]
   L67/44: 52 shapes  texts=[]
   L68/20: 12 shapes  texts=[]
   L68/16: 2 shapes  texts=[]
   L68/5: 2 shapes  texts=['VGND', 'VPWR']
   L64/16: 1 shapes  texts=[]
   L64/5: 1 shapes  texts=['VPB']
   L122/16: 1 shapes  texts=[]
   L236/0: 1 shapes  texts=[]
   L66/20: 19 shapes  texts=[]
   L64/59: 1 shapes  texts=['VNB']
   L81/4: 1 shapes  texts=[]
   L94/20: 1 shapes  texts=[]
   L93/44: 1 shapes  texts=[]
   L65/20: 8 shapes  texts=[]
   L64/20: 1 shapes  texts=[]
   L78/44: 2 shapes  texts=[]
   L95/20: 2 shapes  texts=[]
   L66/44: 54 shapes  texts=[]
   L83/44: 1 shapes  texts=['dfrtp_2']
   L67/16: 9 shapes  texts=[]
   L67/5: 9 shapes  texts=['Q', 'Q', 'Q', 'Q', 'RESET_B', 'D', 'CLK', 'CLK', 'RESET_B']
```
"""

import klayout.db as db

ly = db.Layout()
ly.read("samples/sample.gds")

for name in [
    "sky130_fd_sc_hd__nand2_2",
    "VIA_M1M2_PR",
    "sky130_fd_sc_hd__dfrtp_2",
]:
    c = ly.cell(name)

    # Print basic information about the cell:
    print("==", name, "bbox", c.bbox(), "insts", c.child_instances())

    # Examine every layer in the layout
    for lidx in ly.layer_indexes():
        info = ly.get_info(lidx)
        sh = c.shapes(lidx)

        if sh.size():
            # Collect any text labels on this layer
            txt = [s.text.string for s in sh.each() if s.is_text()]

            print(f"   L{info.layer}/{info.datatype}: {sh.size()} shapes  texts={txt}")
