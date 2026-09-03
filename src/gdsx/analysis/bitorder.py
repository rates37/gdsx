"""Bit-order recovery: give an order to registers topology could not order.

The index in a layout label (`q_3`, `O[3]`, `sum_12`) is the designer's own,
so it is the one piece of bit-order information that does not have to be
inferred. Where it is missing, one-hot probing recovers it instead.
"""

from __future__ import annotations
import random
import re

from ..netlist import Netlist
from ..sim import Simulator
from ..sim.state import load_state
from .registers import Register

_INDEXED = re.compile(r"^(.*?)[_\[](\d+)\]?$")


def indexed_ports(nl: Netlist, direction: str = "output") -> dict[str, dict[int, str]]:
    """Ports that look like the bits of one word: `q_3`, `O[3]`, `sum_12`

    The index in a layout label is the designer's own, so it is the one piece of
    bit-order information that does not have to be inferred
    """
    families: dict[str, dict[int, str]] = {}
    for port, kind in nl.ports.items():
        if kind != direction:
            continue
        match = _INDEXED.match(port)
        if match:
            families.setdefault(match.group(1), {})[int(match.group(2))] = port
    return {base: bits for base, bits in families.items() if len(bits) > 1}


def probe_positions(
    nl: Netlist, register: Register, inputs: dict[str, int] | None = None
):
    """word -> {flop: bit index}, for the words each flop lands on cleanly

    Per word, because one flop legitimately reaches several. E.g., bit 2 of a register
    is bit 2 of the register's own output *and* three bits of the adder it
    feeds. The register word is the one where it moves a single bit
    """
    families = indexed_ports(nl)
    if not families:
        return {}

    simulator = Simulator(nl)
    inputs = inputs or {p: 0 for p, d in nl.ports.items() if d == "input"}
    simulator.state = dict.fromkeys(simulator.state, 0)
    baseline = simulator.settle(inputs)

    positions: dict[str, dict[str, int]] = {base: {} for base in families}
    for flop in register.flops:
        simulator.state = dict.fromkeys(simulator.state, 0)
        simulator.state[flop] = 1
        settled = simulator.settle(inputs)
        for base, bits in families.items():
            moved = [i for i, port in bits.items() if settled[port] != baseline[port]]
            if len(moved) == 1:
                positions[base][flop] = moved[0]
    return {base: found for base, found in positions.items() if found}


def check_order(nl: Netlist, register: Register, base: str, samples: int = 64) -> bool:
    """Confirm a recovered bit order: load a value, read it back off the word"""
    bits = indexed_ports(nl).get(base)
    if not bits or len(bits) < register.width:
        return False
    simulator = Simulator(nl)
    inputs = {p: 0 for p, d in nl.ports.items() if d == "input"}

    span = 1 << register.width
    values = (
        range(span)
        if register.width <= 8
        else [random.Random(0).randrange(span) for _ in range(samples)]
    )
    for value in values:
        load_state(simulator, register, value)
        settled = simulator.settle(inputs)
        read = sum(settled[bits[i]] << i for i in range(register.width))
        if read != value:
            return False
    return True


def _from_positions(register: Register, positions: dict[str, dict[str, int]]):
    """Split the group by which output word each flop lands on, in bit order"""
    usable = {
        base: found
        for base, found in positions.items()
        if len(found) >= 2 and set(found.values()) == set(range(len(found)))
    }

    remaining = set(register.flops)
    made = []
    for base, found in sorted(usable.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if not set(found) <= remaining:
            continue  # overlaps a word already taken; skip rather than guess
        remaining -= set(found)
        made.append(
            Register(
                name=base,
                flops=[flop for flop, _ in sorted(found.items(), key=lambda kv: kv[1])],
                serial_input=register.serial_input,
                kind=register.kind,
                ordered=True,
                order_evidence="probe",
            )
        )
    return made, sorted(remaining)


def resolve_bit_order(nl: Netlist, registers: list[Register]) -> list[Register]:
    """Give an order to registers topology could not order"""
    resolved: list[Register] = []
    for register in registers:
        if register.ordered or register.width < 2:
            resolved.append(register)
            continue

        candidates, leftover = _from_positions(register, probe_positions(nl, register))

        if candidates and all(check_order(nl, c, c.name) for c in candidates):
            resolved.extend(candidates)
            if leftover:
                # whatever did not land on a word is still a group. it just has
                # no order, and saying so is the honest option
                resolved.append(
                    Register(
                        name=f"reg_{leftover[0]}",
                        flops=leftover,
                        serial_input=register.serial_input,
                        kind=register.kind,
                        ordered=False,
                        order_evidence="",
                    )
                )
        else:
            resolved.append(register)
    resolved.sort(key=lambda r: r.name)
    return resolved
