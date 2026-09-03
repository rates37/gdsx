"""The Python REPL panel's bootstrap, exercised without booting Pyodide.

The REPL's session namespace is defined as Python source embedded in
``web/src/worker.ts`` and only ever runs inside a browser, which put it
outside the reach of both test suites: the node suite cannot execute Python
and the library suite never looked at ``web/``. It regressed accordingly --
the namespace was rebuilt on every entry, so no name a player bound survived
to the next line, and nothing failed to say so.

This lifts the embedded source out of the TypeScript file and runs it here
against stubbed design bindings. It tests the console's *semantics* -- what
persists, what a value is, how an error is reported -- not the Pyodide
plumbing around it, which needs a browser.
"""

import json
import re
import sys
import types
from pathlib import Path

import pytest

_WORKER_TS = Path(__file__).resolve().parents[1] / "web" / "src" / "worker.ts"


def _bootstrap_source() -> str:
    """The Python inside ``const REPL_BOOTSTRAP = \\`...\\``` in worker.ts."""
    text = _WORKER_TS.read_text()
    match = re.search(r"const REPL_BOOTSTRAP = `\n(.*?)\n`;", text, re.S)
    assert match is not None, "REPL_BOOTSTRAP not found in worker.ts"
    # Backticks are escaped for the JS template literal they are embedded in.
    return match.group(1).replace("\\`", "`")


class _StubDesign:
    netlist = "NETLIST"
    graph = "GRAPH"

    def simulator(self) -> str:
        return "SIM"


@pytest.fixture
def repl(monkeypatch):
    """Returns ``run(source, handle="H1") -> dict``, the panel's own contract."""
    api = types.ModuleType("gdsx.api")
    api._get = lambda handle: _StubDesign()
    serial = types.ModuleType("gdsx.core.serial")
    serial.to_dict = lambda value: {"stub": True}
    core = types.ModuleType("gdsx.core")
    core.serial = serial
    gdsx = types.ModuleType("gdsx")
    gdsx.api = api

    for name, module in {
        "gdsx": gdsx,
        "gdsx.api": api,
        "gdsx.core": core,
        "gdsx.core.serial": serial,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    namespace: dict = {}
    exec(compile(_bootstrap_source(), "<bootstrap>", "exec"), namespace)
    evaluate = namespace["_gdsx_repl_eval"]

    def run(source: str, handle: str = "H1") -> dict:
        return json.loads(evaluate(handle, source))

    return run


def test_a_bound_name_survives_to_the_next_entry(repl):
    """The bug this file exists for: `a = 1` then `print(a)` used to raise."""
    assert repl("a = 1")["repr"] == ""
    assert repl("print(a)")["stdout"] == "1\n"
    assert repl("a + 41")["repr"] == "42"


def test_definitions_persist_and_stay_callable(repl):
    assert repl("def double(x):\n    return x * 2")["error"] is None
    assert repl("double(21)")["repr"] == "42"


def test_the_design_bindings_are_in_scope(repl):
    assert repl("nl")["repr"] == "'NETLIST'"
    assert repl("graph")["repr"] == "'GRAPH'"
    assert repl("sim")["repr"] == "'SIM'"


def test_a_block_ending_in_an_expression_reports_its_value(repl):
    assert repl("b = 2\nc = 3\nb * c")["repr"] == "6"


def test_statements_still_run_and_print(repl):
    assert repl("for i in range(3):\n    print(i)")["stdout"] == "0\n1\n2\n"


def test_errors_are_reported_rather_than_raised(repl):
    assert repl("a ===")["error"].startswith("SyntaxError:")
    assert repl("1 / 0")["error"] == "ZeroDivisionError: division by zero"


def test_state_survives_an_error(repl):
    repl("kept = 7")
    repl("1 / 0")
    assert repl("kept")["repr"] == "7"


def test_reset_restores_a_clobbered_binding(repl):
    assert repl("nl = None\nreset()\nnl")["repr"] == "'NETLIST'"


def test_each_design_gets_its_own_namespace(repl):
    """Opening another puzzle must not inherit names bound against the last."""
    repl("only_in_first = 1", handle="H1")
    assert repl("only_in_first", handle="H2")["error"] == (
        "NameError: name 'only_in_first' is not defined"
    )
    assert repl("only_in_first", handle="H1")["repr"] == "1"