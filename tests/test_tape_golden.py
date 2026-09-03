"""Enforce the golden gate tapes and traces recorded in tests/golden/

See scripts/tape_golden.py. The trace half is the contract the TypeScript
executor will be held to when it lands, so it is enforced from the moment it is
recorded.
"""

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "tape_golden.py"
_spec = importlib.util.spec_from_file_location("tape_golden", _SCRIPT)
tape_golden = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tape_golden)


def test_tapes_and_traces_match_golden():
    assert tape_golden.check() == 0
