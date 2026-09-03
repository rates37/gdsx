"""Enforce the golden CLI outputs recorded in tests/golden/ (see scripts/golden.py)."""

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "golden.py"
_spec = importlib.util.spec_from_file_location("golden", _SCRIPT)
golden = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(golden)


def test_golden_outputs_match():
    assert golden.check() == 0
