from pathlib import Path

import pytest

import fixtures
from gdsx import config, loader, netlist

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "sample.gds"
PUZZLE = Path(__file__).resolve().parents[1] / "samples" / "puzzle.gds"


@pytest.fixture(scope="session")
def tech():
    return config.load()


@pytest.fixture(scope="session")
def sample(tech):
    return loader.load(SAMPLE, tech)


@pytest.fixture(scope="session")
def sample_netlist(sample):
    return netlist.build(sample)


@pytest.fixture(scope="session")
def puzzle_netlist(tech):
    """The 728-instance design. Session scoped."""
    return netlist.build(loader.load(PUZZLE, tech))


@pytest.fixture(scope="session")
def build(tmp_path_factory, tech):
    """build(maker) -> Netlist for one of the synthetic fixtures"""
    outdir = tmp_path_factory.mktemp("fixtures")
    cache = {}

    def _build(maker):
        if maker.__name__ not in cache:
            path = maker(outdir / f"{maker.__name__}.gds")
            cache[maker.__name__] = netlist.build(loader.load(path, tech))
        return cache[maker.__name__]

    return _build


@pytest.fixture(scope="session")
def make():
    return fixtures
