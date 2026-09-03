"""Shared rendering primitives, so nine subjects stop inventing nine styles."""

from __future__ import annotations

from typing import Any, Iterable


def table(headers: Iterable[str], rows: Iterable[Iterable[Any]]) -> str:
    """A left-aligned, whitespace-column table as plain text."""
    head = [str(h) for h in headers]
    body = [[str(cell) for cell in row] for row in rows]

    widths = [len(h) for h in head]
    for row in body:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def line(cells: list[str]) -> str:
        return "  ".join(cell.ljust(w) for cell, w in zip(cells, widths))

    lines = [line(head), line(["-" * w for w in widths])]
    lines.extend(line(row) for row in body)
    return "\n".join(lines)


TreeNode = "str | tuple[str, list['TreeNode']]"


def tree(node: TreeNode, indent: str = "") -> str:
    """A nested `(label, children)` structure as an indented tree.

    A bare `str` is a leaf. `children` is a list of further nodes.
    """
    if isinstance(node, str):
        return f"{indent}{node}"

    label, children = node
    lines = [f"{indent}{label}"]
    lines.extend(tree(child, indent + "  ") for child in children)
    return "\n".join(lines)


def kv(pairs: Iterable[tuple[str, Any]]) -> str:
    """`key: value` lines, keys left-aligned to the widest."""
    items = [(str(k), v) for k, v in pairs]
    width = max((len(k) for k, _ in items), default=0)
    return "\n".join(f"{k.ljust(width)}: {v}" for k, v in items)
