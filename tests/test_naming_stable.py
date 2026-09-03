"""Extraction names are frozen, and changing one is a deliberate act.

Net names (`n149`) and instance names (`dfrtp_2_83`) are assigned during
extraction. They are quoted in hint text, in walkthrough docs, in the puzzle
content and in players' own notes, so a change to shape iteration order that
renumbered them would invalidate all of it silently.

The hashes below are the contract. If one fails, extraction naming moved:
work out why before touching the expected value, and change it only as a
reviewed decision, never to make the test pass.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gdsx.core.context import Design
from gdsx.netlist import naming_digest

SAMPLE = Path("samples/sample.gds")
PUZZLE = Path("samples/puzzle.gds")

# sha256 of the name digest below, frozen when canonical cluster order
# landed. The pure and klayout backends both produce these.
EXPECTED = {
    SAMPLE: "30a2c06da6e5d9959dc4fb8884794ef88731c5c4b351748dc2453e0cc1562d20",
    PUZZLE: "b97aabe1c07bc4d373f9292a7c7fd3d519a8c20bc8cf2aa20070dc42d1245f47",
}


def _digest(path: Path) -> str:
    return naming_digest(Design.open(path).netlist)


def test_sample_names_are_unchanged():
    assert _digest(SAMPLE) == EXPECTED[SAMPLE]


@pytest.mark.slow
def test_puzzle_names_are_unchanged():
    assert _digest(PUZZLE) == EXPECTED[PUZZLE]
