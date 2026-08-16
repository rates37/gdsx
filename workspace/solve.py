import numpy as np
from scipy.optimize import milp, LinearConstraint, Bounds


counters = {
    "dfrtp_2_22 / dfrtp_2_23": [7, 17, 18, 29, 30, 41, 42],
    "dfrtp_2_19 / dfrtp_2_24": [8, 9, 19, 20, 31],
    "dfrtp_2_30 / dfrtp_2_31": [78, 79, 80, 89, 90, 101, 111, 112],
    "dfrtp_2_28 / dfrtp_2_29": [0, 1, 2, 3, 4, 11, 12, 14, 15, 22, 23, 33, 34, 45],
    "dfrtp_2_11 / dfrtp_2_12": [37, 38, 39, 48, 59, 60, 61, 72, 81, 82, 83],
    "dfrtp_2_7 / dfrtp_2_8": [13, 24, 35, 44, 46, 55, 56, 57],
    "dfrtp_2_13 / dfrtp_2_15": [91, 102, 103, 113],
    "dfrtp_2_14 / dfrtp_2_16": [63, 64, 65, 74, 85, 96, 107, 108, 109],
    "dfrtp_2_36 / dfrtp_2_39": [
        10,
        21,
        32,
        40,
        43,
        49,
        50,
        51,
        52,
        53,
        54,
        62,
        73,
        84,
        92,
        93,
        94,
        95,
        104,
        105,
        106,
        114,
        115,
        116,
        117,
        118,
        119,
        120,
    ],
    "dfrtp_2_32 / dfrtp_2_35": [
        5,
        6,
        16,
        25,
        26,
        27,
        28,
        36,
        47,
        58,
        66,
        67,
        68,
        69,
        70,
        71,
        77,
        88,
        99,
        100,
        110,
    ],
    "dfrtp_2_45 / dfrtp_2_46": [75, 76, 86, 87, 97, 98],
    "dfrtp_2_70 / dfrtp_2_71": [5, 16, 27, 38, 49, 60, 71, 82, 93, 104, 115],
    "dfrtp_2_68 / dfrtp_2_69": [4, 15, 26, 37, 48, 59, 70, 81, 92, 103, 114],
    "dfrtp_2_74 / dfrtp_2_75": [7, 18, 29, 40, 51, 62, 73, 84, 95, 106, 117],
    "dfrtp_2_72 / dfrtp_2_73": [6, 17, 28, 39, 50, 61, 72, 83, 94, 105, 116],
    "dfrtp_2_62 / dfrtp_2_63": [1, 12, 23, 34, 45, 56, 67, 78, 89, 100, 111],
    "dfrtp_2_56 / dfrtp_2_57": [0, 11, 22, 33, 44, 55, 66, 77, 88, 99, 110],
    "dfrtp_2_66 / dfrtp_2_67": [3, 14, 25, 36, 47, 58, 69, 80, 91, 102, 113],
    "dfrtp_2_64 / dfrtp_2_65": [2, 13, 24, 35, 46, 57, 68, 79, 90, 101, 112],
    "dfrtp_2_76 / dfrtp_2_78": [9, 20, 31, 42, 53, 64, 75, 86, 97, 108, 119],
    "dfrtp_2_79 / dfrtp_2_80": [8, 19, 30, 41, 52, 63, 74, 85, 96, 107, 118],
    "dfrtp_2_81 / dfrtp_2_84": [10, 21, 32, 43, 54, 65, 76, 87, 98, 109, 120],
}

names = list(counters.keys())
cycles = sorted({c for cc in counters.values() for c in cc})
idx = {c: i for i, c in enumerate(cycles)}

A = np.zeros((len(names), len(cycles)))
for r, n in enumerate(names):
    for c in counters[n]:
        A[r, idx[c]] = 1

res = milp(
    c=np.ones(len(cycles)),
    integrality=np.ones(len(cycles)),
    bounds=Bounds(0, 1),
    constraints=LinearConstraint(A, lb=2, ub=2),
)

if not res.success:
    print("No solution found: ", res.message)
active_cycles = sorted(c for c in cycles if res.x[idx[c]]> 0)
print(f"Total pulses: {len(active_cycles)}")
print(f"Solution: {active_cycles}")
