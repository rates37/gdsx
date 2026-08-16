import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from harness import get_fresh_sim
COUNTER_1 = ['dfrtp_2_51', 'dfrtp_2_41', 'dfrtp_2_40', 'dfrtp_2_47']
COUNTER_2 = ['dfrtp_2_20', 'dfrtp_2_26', 'dfrtp_2_25', 'dfrtp_2_17']

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200

    sim = get_fresh_sim()
    print("cycle | counter_1 | counter_2")
    for i in range(n):
        v = sim.step({'clk': 0, 'rst_n': 1, 'enable': 1, 'I': 0})
        c1 = ''.join(str(sim.state[c]) for c in COUNTER_1)
        c2 = ''.join(str(sim.state[c]) for c in COUNTER_2)
        # i+1 since first clock cycle was pulsing reset
        print(f"{i+1:6d}| {c1} ({int(c1, 2):2d}) | {c2} ({int(c2, 2):2d})")