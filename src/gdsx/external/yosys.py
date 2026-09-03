"""Run yosys scripts. The only place gdsx invokes the yosys binary"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


class YosysUnavailable(RuntimeError):
    """yosys is not on PATH."""


class YosysFailed(RuntimeError):
    """yosys ran and exited non-zero."""

    def __init__(self, stdout: str, stderr: str, script: str):
        super().__init__(f"yosys failed:\n{stdout}\n{stderr}")
        self.stdout = stdout
        self.stderr = stderr
        self.script = script


def available() -> bool:
    return shutil.which("yosys") is not None


def run(script: str, *, cwd: Path | None = None, timeout: float = 300) -> str:
    """Run a yosys script. Raises YosysUnavailable or YosysFailed.

    Never returns an error string and never returns None.
    """
    if not available():
        raise YosysUnavailable("yosys not found on PATH")
    with tempfile.NamedTemporaryFile("w", suffix=".ys", delete=False) as handle:
        handle.write(script)
        script_path = Path(handle.name)
    try:
        proc = subprocess.run(
            ["yosys", str(script_path)],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout,
        )
    finally:
        script_path.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise YosysFailed(proc.stdout, proc.stderr, script)
    return proc.stdout + proc.stderr
