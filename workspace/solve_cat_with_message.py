import os
import sys
sys.path.insert(0, os.path.dirname(__file__))
from harness import get_fresh_sim

KEY = "00000001010" \
+     "10000100000" \
+     "00000001010" \
+     "10100000000" \
+     "00001010000" \
+     "00100000100" \
+     "00001000001" \
+     "01000010000" \
+     "00010000001" \
+     "00000100100" \
+     "01010000000"
bits = [int(c) for c in KEY]
sim = get_fresh_sim()
found = False

for i in range(len(bits)+20):
    b = bits[i] if i < len(bits) else 0
    v = sim.step({'clk':0,'rst_n':1,'enable':1,'I': b})
    if v['success'] and not found:
        print(f"Success went high on bit {i}")
        found = True
    if found:
        c = sum(v[f'O[{i}]'] << i for i in range(8))
        print(chr(c), end="")
print()
