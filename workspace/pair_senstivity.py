import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from harness import GRAPH, get_fresh_sim

# manually placed order so that adjacent ones are the registers that pair up to form each saturating 2 bit counter
N96 = [
    "dfrtp_2_22",
    "dfrtp_2_23",
    "dfrtp_2_19",
    "dfrtp_2_24",
    "dfrtp_2_31",
    "dfrtp_2_30",
    "dfrtp_2_28",
    "dfrtp_2_29",
    "dfrtp_2_11",
    "dfrtp_2_12",
    "dfrtp_2_7",
    "dfrtp_2_8",
    "dfrtp_2_13",
    "dfrtp_2_15",
    "dfrtp_2_16",
    "dfrtp_2_14",
    "dfrtp_2_39",
    "dfrtp_2_36",
    "dfrtp_2_32",
    "dfrtp_2_35",
    "dfrtp_2_45",
    "dfrtp_2_46",
]
N136 = [
    "dfrtp_2_70",
    "dfrtp_2_71",
    "dfrtp_2_68",
    "dfrtp_2_69",
    "dfrtp_2_74",
    "dfrtp_2_75",
    "dfrtp_2_73",
    "dfrtp_2_72",
    "dfrtp_2_63",
    "dfrtp_2_62",
    "dfrtp_2_57",
    "dfrtp_2_56",
    "dfrtp_2_67",
    "dfrtp_2_66",
    "dfrtp_2_64",
    "dfrtp_2_65",
    "dfrtp_2_78",
    "dfrtp_2_76",
    "dfrtp_2_79",
    "dfrtp_2_80",
    "dfrtp_2_81",
    "dfrtp_2_84",
]

ALL_FFS = N96 + N136

# SUP[f] := set of FF names that f's D pin combinationally depend on
SUP = {f: GRAPH.d_support(f) for f in ALL_FFS}


def pair_up(ffs):
    remaining = set(ffs)
    pairs = []
    for f in ffs:
        if f not in remaining:
            continue

        twin = next(g for g in remaining if g != f and g in SUP[f] and f in SUP[g])
        pairs.append(tuple(sorted((f, twin))))
        remaining -= {f, twin}
    return pairs


PAIRS_1 = pair_up(N96)
PAIRS_2 = pair_up(N136)
CYCLES = 121  # write window is 0-120


def pulse_at(cycle: int):
    sim = get_fresh_sim()
    for i in range(CYCLES):
        sim.step({"clk": 0, "rst_n": 1, "enable": 1, "I": 1 if i == cycle else 0})
    return dict(sim.state)


if __name__ == "__main__":
    baseline = pulse_at(
        3737
    )  # any number over 120 will be treated as all 0's in the 0-120 window
    sensitivity = {p: [] for p in PAIRS_1 + PAIRS_2}
    for i in range(CYCLES):
        st = pulse_at(i)
        for p in PAIRS_1 + PAIRS_2:
            if st[p[0]] != baseline[p[0]] or st[p[1]] != baseline[p[1]]:
                sensitivity[p].append(i)

    print("N96 pairs:")
    for p in PAIRS_1:
        print(f"{p[0]:12s}/{p[1]:12s} is sensitive on cycles: {sensitivity[p]}")
    print()
    print("N136 pairs")
    for p in PAIRS_2:
        print(f"{p[0]:12s}/{p[1]:12s} is sensitive on cycles: {sensitivity[p]}")

    print()
    # sanity check:
    insensitive = [p for p, c in sensitivity.items() if len(c) == 0]
    if insensitive:
        print(f"{len(insensitive)} pairs didn't react to any pulses")
