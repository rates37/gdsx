"""Enforce the golden topological orders recorded in tests/golden/.

See scripts/topo_golden.py. Any valid topological order simulates the same, so
only this can catch a reordering.
"""

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "topo_golden.py"
_spec = importlib.util.spec_from_file_location("topo_golden", _SCRIPT)
topo_golden = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(topo_golden)


def test_topological_order_matches_golden():
    assert topo_golden.check() == 0
