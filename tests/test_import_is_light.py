import subprocess
import sys


HEAVY = ("typer", "rich", "klayout", "subprocess")


def imported_by(statement: str) -> str:
    code = (
        f"import sys; {statement}; "
        f"print([m for m in {HEAVY!r} if m in sys.modules])"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_import_gdsx_is_light():
    """import gdsx must not pull typer, rich, klayout or subprocess."""
    assert imported_by("import gdsx") == "[]"


def test_import_the_api_facade_is_light():
    """The worker imports gdsx.api first and must not pay for the CLI stack.

    Every heavy import in api.py is deferred into the function that needs it,
    so a browser that only ever calls `truth_table` never loads the extractor.
    """
    assert imported_by("import gdsx.api") == "[]"
