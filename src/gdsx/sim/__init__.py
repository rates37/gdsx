"""The gate-level simulator, the gate tape, and register/shift-mode helpers."""

from .execute import TapeExecutor
from .observe import Trace, bus, quiescent
from .simulator import Simulator, UnsupportedCell
from .tape import (
    TAPE_VERSION,
    Flop,
    FlopKind,
    GateTape,
    Op,
    UnconnectedPin,
    UnsupportedFunction,
    compile,
)
