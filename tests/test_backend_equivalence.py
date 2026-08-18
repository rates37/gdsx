"""Differential test: pure backend vs klayout backend, byte-identical output."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from rich.console import Console

from gdsx import geo, netlist
from gdsx.core.context import Design
from gdsx.pins import PinOracle
from gdsx.report import pins as report_pins

SAMPLE = Path("samples/sample.gds")
PUZZLE = Path("samples/puzzle.gds")


@pytest.fixture(autouse=True)
def _restore_backend():
    """geo.use() is process-global, so put it back afterwards"""
    before = geo.backend()
    yield
    geo._backend = before


def _pin_dump(design: Design) -> str:
    """The pin oracle report, rendered as plain text with no color codes"""
    buf = io.StringIO()
    console = Console(file=buf, no_color=True, force_terminal=False, width=100)
    oracle = PinOracle(design.layout, design.macros())
    report_pins.render(console, design.layout, oracle)
    return buf.getvalue()


def _build(path: Path, backend: str) -> tuple[netlist.Netlist, str]:
    geo.use(backend)
    design = Design.open(path)
    return design.netlist, _pin_dump(design)


def _assert_backends_agree(path: Path) -> None:
    klayout_nl, klayout_pins = _build(path, "klayout")
    pure_nl, pure_pins = _build(path, "pure")

    assert netlist.to_verilog(pure_nl) == netlist.to_verilog(klayout_nl)
    assert netlist.to_json(pure_nl) == netlist.to_json(klayout_nl)
    assert netlist.to_dot(pure_nl) == netlist.to_dot(klayout_nl)
    assert pure_pins == klayout_pins


def test_sample_backends_agree():
    _assert_backends_agree(SAMPLE)


@pytest.mark.slow
def test_puzzle_backends_agree():
    _assert_backends_agree(PUZZLE)
