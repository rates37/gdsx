"""Where the bundled data files live.

`config/sky130.yaml` and `config/sky130_fd_sc_hd.cells.json` sit at the repo
root, which every module found with `parents[2]`, correct in a source
checkout, and wrong in an installed wheel, where there is no repo above
`site-packages/gdsx/`. That is why `api.analyse` failed in Pyodide with
`No such file or directory: '/lib/python3.12/config/sky130.yaml'`.

So: `scripts/build_wheel.py` copies the repo's `config/` into
`src/gdsx/data/` (gitignored) before `uv build`, and this looks in the
checkout **first**, so editing `config/sky130.yaml` in a working tree still
takes effect immediately and nothing about a source run changes.
"""

from __future__ import annotations

from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CHECKOUT = _HERE.parents[1] / "config"  # <repo>/config, a source checkout
_PACKAGED = _HERE / "data"  # gdsx/data, inside an installed wheel
# (deliberately not gdsx/config -- that name collides with the config.py module)


def data_file(name: str) -> Path:
    """The path to a bundled data file, checkout copy winning over the wheel's

    Returns the checkout path when the file is in neither, so a missing file
    reports the location a developer would expect to fix.
    """
    checkout = _CHECKOUT / name
    if checkout.exists():
        return checkout
    packaged = _PACKAGED / name
    return packaged if packaged.exists() else checkout
