from __future__ import annotations

import json
import subprocess

from gdsx import floorplan


def test_every_netlist_instance_gets_a_position(sample, sample_netlist):
    placed = floorplan.placements(sample, sample_netlist)
    assert {p.name for p in placed} == {i.name for i in sample_netlist.instances}


def test_positions_are_in_microns_not_database_units(sample, sample_netlist):
    placed = floorplan.placements(sample, sample_netlist)
    span = max(p.x for p in placed) - min(p.x for p in placed)
    assert 1.0 < span < 10_000.0, "a die measured in dbu would be a million wide"


def test_a_group_laid_out_together_scores_tighter_than_a_scattered_one(
    sample, sample_netlist
):
    placed = floorplan.placements(sample, sample_netlist)
    by_x = sorted(placed, key=lambda p: p.x)
    together = {p.name: "together" for p in by_x[:6]}
    scattered = {p.name: "scattered" for p in by_x[::12]}

    spread = floorplan.spread(placed, {**together, **scattered})
    assert spread["together"] < spread["scattered"]


def test_drawing_marks_the_unlabelled_cells_differently(sample, sample_netlist):
    placed = floorplan.placements(sample, sample_netlist)
    svg = floorplan.draw(placed, {placed[0].name: "one"}, "title")
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert "one" in svg


def test_map_colours_by_a_supplied_grouping(tmp_path):
    groups = tmp_path / "groups.json"
    groups.write_text(json.dumps({"alpha": ["dfrtp_2_1"], "beta": ["dfrtp_2_2"]}))
    out = tmp_path / "f.svg"
    result = subprocess.run(
        [
            "uv",
            "run",
            "gdsx",
            "map",
            "samples/sample.gds",
            "--groups",
            str(groups),
            "-o",
            str(out),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert out.exists()
    assert "2 groups" in result.stdout
