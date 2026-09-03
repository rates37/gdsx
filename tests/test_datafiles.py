"""The bundled data files must resolve from a wheel, not just a checkout.

`config/sky130.yaml` and the cell library sit above the package, so resolving
them with `parents[2]` worked in a source tree and silently produced a wheel
that could not analyse anything -- `api.analyse` failed in Pyodide with
`No such file or directory: '/lib/python3.12/config/sky130.yaml'`. These
tests pin both halves of the fix.
"""

import shutil

from gdsx import config, datafiles, liberty


def test_defaults_resolve_in_a_checkout():
    assert config.DEFAULT_CONFIG.exists()
    assert config.DEFAULT_JSON_CONFIG.exists()
    assert liberty.DEFAULT_CELLS.exists()


def test_checkout_copy_wins_over_the_packaged_one():
    # A developer editing config/sky130.yaml must see the edit even when a
    # stale gdsx/data/ copy is lying around from a wheel build.
    assert datafiles.data_file("sky130.yaml") == datafiles._CHECKOUT / "sky130.yaml"


def test_falls_back_to_the_packaged_copy(monkeypatch, tmp_path):
    packaged = tmp_path / "data"
    packaged.mkdir()
    shutil.copy2(config.DEFAULT_CONFIG, packaged / "sky130.yaml")
    monkeypatch.setattr(datafiles, "_CHECKOUT", tmp_path / "no-such-checkout")
    monkeypatch.setattr(datafiles, "_PACKAGED", packaged)

    found = datafiles.data_file("sky130.yaml")
    assert found == packaged / "sky130.yaml"
    assert config.load(found).pdk


def test_missing_everywhere_reports_the_checkout_path(monkeypatch, tmp_path):
    monkeypatch.setattr(datafiles, "_CHECKOUT", tmp_path / "checkout")
    monkeypatch.setattr(datafiles, "_PACKAGED", tmp_path / "packaged")
    assert datafiles.data_file("nope.yaml") == tmp_path / "checkout" / "nope.yaml"
