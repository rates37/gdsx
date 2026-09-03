"""Enforce the golden sensitivity sweeps recorded in tests/golden/

See scripts/sensitivity_golden.py. These are the contract the game's Experiment
Runner is held to: it runs the same sweeps on the gate tape in TypeScript, and
web/scripts/test-experiments.mjs reads these same files.
"""

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sensitivity_golden.py"
_spec = importlib.util.spec_from_file_location("sensitivity_golden", _SCRIPT)
sensitivity_golden = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sensitivity_golden)


def test_sweeps_match_golden():
    assert sensitivity_golden.check() == 0