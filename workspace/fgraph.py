# flop-level dependency report

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from harness import GRAPH


if __name__ == "__main__":
    for f in sorted(
        GRAPH.seq, key=lambda s: (s.rsplit("_", 1)[0], int(s.rsplit("_", 1)[1]))
    ):
        names = sorted(GRAPH.d_support(f))
        print(f"{f}\tD <- {','.join(names)}")
