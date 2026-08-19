"""Enforce the golden tape slices recorded in tests/golden/

See scripts/slice_golden.py. These tables are the contract the bit-parallel
TypeScript evaluator in web/src/notebook/ is held to, so they are enforced from
the moment they are recorded -- the same arrangement tape/trace has for the two
tape executors.
"""

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "slice_golden.py"
_spec = importlib.util.spec_from_file_location("slice_golden", _SCRIPT)
slice_golden = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(slice_golden)


def test_slices_match_golden():
    assert slice_golden.check() == 0