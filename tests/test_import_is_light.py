import subprocess
import sys


def test_import_gdsx_is_light():
    """import gdsx must not pull typer, rich, klayout or subprocess."""
    code = (
        "import sys, gdsx; "
        "print([m for m in ('typer','rich','klayout','subprocess') if m in sys.modules])"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.stdout.strip() == "[]", out.stdout
