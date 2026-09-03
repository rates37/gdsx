"""Enforce that web/src/gdsx-types.ts matches scripts/gen_types.py."""

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "gen_types.py"
_spec = importlib.util.spec_from_file_location("gen_types", _SCRIPT)
gen_types = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen_types)


def test_generated_types_are_not_stale():
    assert gen_types.check() == 0
