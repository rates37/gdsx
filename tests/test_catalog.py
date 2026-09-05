"""`puzzles/catalog.json` is the only source of a level's authored data.

`manifest.json` and `solution.json` are generated from it (`gdsx puzzle
sync`), which is only true as long as something checks. Without these tests a
hand-edit to a generated file works perfectly until the next sync silently
reverts it -- the exact failure mode the catalog was introduced to end, moved
one directory along.

So: every committed manifest and solution must be byte-identical to what the
catalog produces, every level with a `design.gds` must be in the catalog, and
the two things the generator fills in itself (the id, and the manifest schema
version) must actually match the directory they land in.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gdsx import catalog

PUZZLES = Path(__file__).resolve().parents[1] / "puzzles"


@pytest.fixture(scope="module")
def entries() -> list[catalog.Entry]:
    return catalog.load(PUZZLES)


def test_the_committed_files_are_what_the_catalog_produces():
    """`gdsx puzzle sync --check` is clean

    The whole contract in one assertion. If this fails, either someone edited
    a generated file by hand or the catalog changed without a re-sync; the
    fix is `uv run gdsx puzzle sync`, never editing the generated file back.
    """
    result = catalog.sync(PUZZLES, check=True)
    assert result.checked, "the catalog described no files at all"
    assert result.written == [], (
        "these are generated from puzzles/catalog.json and have drifted from it: "
        + ", ".join(str(p) for p in result.written)
    )


def test_every_baked_level_is_in_the_catalog():
    """A level whose prose is not in the one file that holds all the prose"""
    assert catalog.undeclared(PUZZLES) == []


def test_the_catalog_covers_exactly_the_puzzle_directories(entries):
    on_disk = {d.name for d in PUZZLES.iterdir() if d.is_dir()}
    assert {e.id for e in entries} == on_disk


def test_each_manifest_declares_the_directory_it_sits_in(entries):
    """The id is the entry's key, not a field that can disagree with its home"""
    for entry in entries:
        written = json.loads((PUZZLES / entry.id / "manifest.json").read_text())
        assert written["id"] == entry.id
        assert written["schema_version"] == catalog.MANIFEST_SCHEMA_VERSION


def test_every_level_carries_the_fields_the_game_reads(entries):
    """What `web/scripts/puzzle-index.mjs` turns into a catalog entry.

    Not a schema -- the web side already validates difficulty and drops
    anything malformed. This is the narrower claim that no level is missing a
    field the menu or the briefing card would then have to render as blank.
    """
    for entry in entries:
        manifest = entry.manifest
        assert manifest["title"]
        assert manifest["blurb"]
        assert manifest["difficulty"] in {"easy", "medium", "hard"}
        assert isinstance(manifest["par_seconds"], int) and manifest["par_seconds"] > 0
        assert manifest["tools_enabled"]
        assert len(manifest["backstory"]) >= 1
        assert all(p.strip() for p in manifest["backstory"])


def test_solutions_keep_their_own_schema_version(entries):
    """Versions 1, 2 and 3 are all still in use and all still read

    A generator that stamped one version onto every solution would silently
    claim v3 semantics for a v1 file. `puzzle.py` refuses a version it does
    not know, so this would fail loudly there -- but it should not get that
    far.
    """
    versions = {e.id: e.solution["schema_version"] for e in entries}
    assert set(versions.values()) <= {1, 2, 3}
    assert versions["original-puzzle"] == 1


def test_sync_is_idempotent(tmp_path):
    """A second sync writes nothing -- files are compared before being written"""
    assert catalog.sync(PUZZLES, check=True).clean


def test_a_catalog_with_no_puzzles_is_an_error(tmp_path):
    (tmp_path / "catalog.json").write_text(json.dumps({"puzzles": []}))
    with pytest.raises(catalog.CatalogError, match="lists no puzzles"):
        catalog.load(tmp_path)


def test_a_missing_catalog_is_an_error(tmp_path):
    with pytest.raises(catalog.CatalogError, match="no catalog"):
        catalog.load(tmp_path)


def test_a_duplicate_id_is_an_error(tmp_path):
    entry = {"id": "x", "manifest": {"title": "X"}, "solution": {"schema_version": 3}}
    (tmp_path / "catalog.json").write_text(json.dumps({"puzzles": [entry, entry]}))
    with pytest.raises(catalog.CatalogError, match="appears twice"):
        catalog.load(tmp_path)


def test_an_entry_missing_a_block_is_an_error(tmp_path):
    (tmp_path / "catalog.json").write_text(
        json.dumps({"puzzles": [{"id": "x", "manifest": {"title": "X"}}]})
    )
    with pytest.raises(catalog.CatalogError, match="no solution block"):
        catalog.load(tmp_path)


def test_an_entry_with_no_directory_is_an_error(tmp_path):
    (tmp_path / "catalog.json").write_text(
        json.dumps(
            {"puzzles": [{"id": "ghost", "manifest": {"title": "X"}, "solution": {}}]}
        )
    )
    with pytest.raises(catalog.CatalogError, match="no directory"):
        catalog.sync(tmp_path, check=True)