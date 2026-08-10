"""Formal equivalence against a reference RTL via yosys"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import primitives

SCRIPT = """\
read_verilog {primitives} {reference}
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


#: The primitive library is read on both sides: a reference may be pure RTL, or
#: it may be a hybrid that still instantiates gates.
#: Same construction, minus the sequential machinery
COMBINATIONAL_SCRIPT = """\
read_verilog {primitives} {reference}
prep -top {ref_top} -flatten
design -stash gold

read_verilog {primitives} {extracted}
prep -top {gate_top} -flatten
design -stash gate

design -copy-from gold -as gold {ref_top}
design -copy-from gate -as gate {gate_top}
miter -equiv -flatten -make_assert gold gate miter
hierarchy -top miter
sat -verify -prove-asserts miter
"""


class YosysMissing(Exception):
    pass


def available() -> bool:
    return shutil.which("yosys") is not None


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


def _run(script_text: str, workdir: Path, tag: str) -> EquivalenceResult:
    if not available():
        raise YosysMissing("yosys not found, please install it")
    workdir.mkdir(parents=True, exist_ok=True)
    script = workdir / f"{tag}.ys"
    script.write_text(script_text)
    proc = subprocess.run(
        ["yosys", str(script)], capture_output=True, text=True, cwd=Path.cwd()
    )
    log = proc.stdout + proc.stderr
    (workdir / f"{tag}.log").write_text(log)
    return EquivalenceResult(proven=proc.returncode == 0, log=log)


def _primitive_library(workdir: Path) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    prims = workdir / "primitives.v"
    prims.write_text(primitives.verilog())
    return prims


def equivalence(
    extracted: Path, reference: Path, top: str, workdir: Path, seq: int = 4
) -> EquivalenceResult:
    prims = _primitive_library(workdir)
    return _run(
        SCRIPT.format(
            reference=reference, extracted=extracted, primitives=prims, top=top, seq=seq
        ),
        workdir,
        "equiv",
    )


def combinational_equivalence(
    gate_verilog: str,
    gate_top: str,
    reference_verilog: str,
    ref_top: str,
    workdir: Path,
    tag: str = "cone",
) -> EquivalenceResult:
    """Prove a cone of gates computes the same thing as a reference expression"""
    prims = _primitive_library(workdir)
    gate = workdir / f"{tag}.gate.v"
    gate.write_text(gate_verilog)
    ref = workdir / f"{tag}.ref.v"
    ref.write_text(reference_verilog)
    return _run(
        COMBINATIONAL_SCRIPT.format(
            reference=ref,
            extracted=gate,
            primitives=prims,
            gate_top=gate_top,
            ref_top=ref_top,
        ),
        workdir,
        tag,
    )
