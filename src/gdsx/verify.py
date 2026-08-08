"""Formal equivalence against a reference RTL via yosys"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import primitives

SCRIPT = """\
read_verilog {reference}
prep -top {top} -flatten
async2sync
design -stash gold

read_verilog {primitives} {extracted}
prep -top {top} -flatten
async2sync
design -stash gate

design -copy-from gold -as gold {top}
design -copy-from gate -as gate {top}
miter -equiv -flatten -make_assert gold gate miter
hierarchy -top miter
sat -verify -prove-asserts -tempinduct -set-init-zero -seq {seq} miter
"""


class YosysMissing(Exception):
    pass


@dataclass
class EquivalenceResult:
    proven: bool
    log: str

    @property
    def summary(self) -> str:
        for line in reversed(self.log.splitlines()):
            if "SUCCESS" in line or "FAIL" in line or "ERROR" in line:
                return line.strip()
        return "no verdict from yosys"


def equivalence(
    extracted: Path, reference: Path, top: str, workdir: Path, seq: int = 4
) -> EquivalenceResult:
    if shutil.which("yosys") is None:
        raise YosysMissing("yosys not found, please install it")

    workdir.mkdir(parents=True, exist_ok=True)
    prims = workdir / "primitives.v"
    prims.write_text(primitives.verilog())
    script = workdir / "equiv.ys"
    script.write_text(
        SCRIPT.format(
            reference=reference, extracted=extracted, primitives=prims, top=top, seq=seq
        )
    )

    proc = subprocess.run(
        ["yosys", str(script)], capture_output=True, text=True, cwd=Path.cwd()
    )
    log = proc.stdout + proc.stderr
    (workdir / "equiv.log").write_text(log)
    return EquivalenceResult(proven=proc.returncode == 0, log=log)
