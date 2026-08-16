# load puzzle netlist once, cache it, wrap simulator

import os
import pickle
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from gdsx import config, loader, netlist
from gdsx.sim import Simulator


CACHE_PATH = os.path.join(os.path.dirname(__file__), "puzzle.netlist.pkl")


def load() -> netlist.Netlist:
    # return cached netlist if it exists:
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, "rb") as f:
            return pickle.load(f)

    # parse gds and create netlist:
    nl = netlist.build(loader.load("samples/puzzle.gds", config.load()))

    # cache loaded netlist for future use:
    with open(CACHE_PATH, "wb") as f:
        pickle.dump(nl, f)
    return nl


NETLIST = load()


def get_fresh_sim() -> Simulator:
    # create sim from netlist:
    sim = Simulator(NETLIST)

    # reset all sim registers to 0:
    sim.reset()

    # pulse rst_n once
    sim.step({"clk": 0, "rst_n": 0, "enable": 1, "I": 0})

    return sim


def run(bits: str | list[int], cycles: int | None = None):
    # drive I with `bits` (list or string of 0/1s), pad with 0 to `cycles`. return (sim, list of settled value dicts per cycle)

    # convert to list of ints:
    bits = [int(c) for c in bits] if isinstance(bits, str) else bits
    assert (b in {0, 1} for b in bits)

    if cycles is None:
        cycles = len(bits)

    sim = get_fresh_sim()
    trace = []
    for i in range(cycles):
        b = bits[i] if i < len(bits) else 0
        trace.append(sim.step({"clk": 0, "rst_n": 1, "enable": 1, "I": b}))
    return sim, trace
