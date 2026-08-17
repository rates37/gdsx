from __future__ import annotations

from gdsx.report import tables


def test_table_aligns_columns():
    text = tables.table(["name", "n"], [["short", 1], ["a-longer-one", 22]])
    lines = text.splitlines()

    assert lines[0].startswith("name")
    assert lines[1].startswith("----")
    assert all(len(line) == len(lines[0]) for line in lines)


def test_tree_indents_children():
    text = tables.tree(("root", ["leaf", ("branch", ["twig"])]))

    assert text == "root\n  leaf\n  branch\n    twig"


def test_kv_aligns_keys():
    text = tables.kv([("a", 1), ("longer", 2)])

    assert text == "a     : 1\nlonger: 2"
