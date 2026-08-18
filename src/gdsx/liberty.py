"""Cell behaviour, read from the library's Liberty data

Distilled library data lives in config/sky130_fd_sc_hd.cells.json.
See tools/fetch_liberty.py for how it is produced
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import datafiles

DEFAULT_CELLS = datafiles.data_file("sky130_fd_sc_hd.cells.json")

# Boolean expression parsing

Expr = tuple  # ("var", name) | ("not", e) | ("and"|"or"|"xor", a, b) | ("const", 0|1)

TOKEN = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*|[01]|[!&|^*+()'])")


class ExpressionError(Exception):
    pass


def tokenise(text: str) -> list[str]:
    tokens, pos = [], 0
    while pos < len(text):
        match = TOKEN.match(text, pos)
        if not match:
            if text[pos:].strip() == "":
                break
            raise ExpressionError(f"cannot tokenise {text[pos:]!r} in {text!r}")
        tokens.append(match.group(1))
        pos = match.end()
    return tokens


class _Parser:
    STARTS_TERM = re.compile(r"[A-Za-z_01(!]")

    def __init__(self, tokens: list[str]):
        self.tokens = tokens
        self.i = 0

    def peek(self) -> str | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self) -> str:
        if self.i >= len(self.tokens):
            raise ExpressionError("expression ended early")
        token = self.tokens[self.i]
        self.i += 1
        return token

    def parse(self) -> Expr:
        expr = self.or_expr()
        if self.peek() is not None:
            raise ExpressionError(f"trailing {self.peek()!r}")
        return expr

    def or_expr(self) -> Expr:
        node = self.xor_expr()
        while self.peek() in ("|", "+"):
            self.take()
            node = ("or", node, self.xor_expr())
        return node

    def xor_expr(self) -> Expr:
        node = self.and_expr()
        while self.peek() == "^":
            self.take()
            node = ("xor", node, self.and_expr())
        return node

    def and_expr(self) -> Expr:
        node = self.unary()
        while True:
            token = self.peek()
            if token in ("&", "*"):
                self.take()
            elif token is None or not self.STARTS_TERM.match(token):
                break  # implicit AND only when a new term actually starts
            node = ("and", node, self.unary())
        return node

    def unary(self) -> Expr:
        if self.peek() == "!":
            self.take()
            return ("not", self.unary())
        return self.postfix()

    def postfix(self) -> Expr:
        node = self.primary()
        while self.peek() == "'":
            self.take()
            node = ("not", node)
        return node

    def primary(self) -> Expr:
        token = self.take()
        if token == "(":
            node = self.or_expr()
            if self.take() != ")":
                raise ExpressionError("unbalanced parentheses")
            return node
        if token in ("0", "1"):
            return ("const", int(token))
        if not token[0].isalpha() and token[0] != "_":
            raise ExpressionError(f"unexpected {token!r}")
        return ("var", token)


def parse(text: str) -> Expr:
    return _Parser(tokenise(text)).parse()


def variables(expr: Expr) -> set[str]:
    if expr[0] == "var":
        return {expr[1]}
    if expr[0] == "const":
        return set()
    return set().union(*(variables(child) for child in expr[1:]))


def evaluate(expr: Expr, values: dict[str, int]) -> int:
    kind = expr[0]
    if kind == "var":
        return values[expr[1]]
    if kind == "const":
        return expr[1]
    if kind == "not":
        return 1 - evaluate(expr[1], values)
    left, right = evaluate(expr[1], values), evaluate(expr[2], values)
    return {"and": left & right, "or": left | right, "xor": left ^ right}[kind]


def to_verilog(expr: Expr) -> str:
    kind = expr[0]
    if kind == "var":
        return expr[1]
    if kind == "const":
        return f"1'b{expr[1]}"
    if kind == "not":
        return f"~{to_verilog(expr[1])}"
    op = {"and": "&", "or": "|", "xor": "^"}[kind]
    return f"({to_verilog(expr[1])} {op} {to_verilog(expr[2])})"


# cell model


@dataclass(frozen=True)
class Sequential:
    """A Liberty `ff` or `latch` group"""

    state_vars: tuple[str, ...]  # e.g. ("IQ", "IQ_N")
    clocked_on: Expr
    next_state: Expr
    clear: Expr | None = None
    preset: Expr | None = None
    is_latch: bool = False

    @property
    def state_var(self) -> str:
        return self.state_vars[0]


@dataclass(frozen=True)
class Cell:
    """What a library cell does, independent of drive strength"""

    name: str  # base name, e.g. "nand2"
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    power: tuple[str, ...]
    functions: dict[str, Expr]  # output pin -> expression
    sequential: Sequential | None = None
    tristate: bool = False

    @property
    def is_sequential(self) -> bool:
        return self.sequential is not None

    @property
    def has_behaviour(self) -> bool:
        """False for fill/tap/decap/antenna cells, which do nothing"""
        return bool(self.functions) and not self.tristate

    def evaluate(self, values: dict[str, int]) -> dict[str, int]:
        """Output pin values, given input pins (and state vars if sequential)"""
        return {pin: evaluate(expr, values) for pin, expr in self.functions.items()}

    def direction(self, pin: str) -> str:
        if pin in self.outputs:
            return "output"
        if pin in self.power:
            return "power"
        return "input"


def _expr(raw: dict, key: str) -> Expr | None:
    text = raw.get(key)
    return parse(text) if text else None


def _build(name: str, raw: dict) -> Cell:
    inputs, outputs, functions = [], [], {}
    power = list(raw.get("power", ()))
    for pin, info in raw.get("pins", {}).items():
        direction = info.get("direction", "input")
        if direction == "output":
            outputs.append(pin)
            if "function" in info:
                functions[pin] = parse(info["function"])
        elif direction == "internal":
            continue
        else:
            inputs.append(pin)

    sequential = None
    for kind in ("ff", "latch"):
        if kind in raw:
            group = raw[kind]
            sequential = Sequential(
                state_vars=tuple(group.get("state_vars", ())),
                # ff groups say clocked_on/next_state, latch groups say enable/data_in
                clocked_on=_expr(group, "clocked_on") or _expr(group, "enable"),
                next_state=_expr(group, "next_state") or _expr(group, "data_in"),
                clear=_expr(group, "clear"),
                preset=_expr(group, "preset"),
                is_latch=kind == "latch",
            )

    tristate = any("three_state" in info for info in raw.get("pins", {}).values())
    return Cell(
        name=name,
        inputs=tuple(sorted(inputs)),
        outputs=tuple(sorted(outputs)),
        power=tuple(sorted(power)),
        functions=functions,
        sequential=sequential,
        tristate=tristate,
    )


@lru_cache(maxsize=None)
def library(path: str | None = None) -> dict[str, Cell]:
    """Base cell name -> Cell, parsed from the distilled Liberty data"""
    raw = json.loads(Path(path or DEFAULT_CELLS).read_text())
    return {name: _build(name, data) for name, data in raw["cells"].items()}
