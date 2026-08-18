"""The design handle: a source, its netlist, its graph, and memoised analyses

One `Design` is opened per thing being looked at, and every question is asked of
it. Nothing here recomputes: an answer is worked out on first request and kept
until `invalidate` says otherwise, so nine commands over one design cost one
extraction and one `Graph`.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from .graph import Graph
from .netlist import Netlist

if TYPE_CHECKING:  # types only, so none of these are imported at runtime
    from ..analyse import Register
    from ..config import TechConfig
    from ..guards import Guards
    from ..interface import Interface
    from ..sim import Simulator


class NoLayoutAvailable(RuntimeError):
    """This design has no layout: it came from a netlist, not from a GDS"""


# What each cached entry is derived from. `invalidate("netlist")` has to take
# the graph and every analysis with it, or the next caller gets an answer about
# a netlist that no longer exists.
_DEPENDENTS: dict[str, tuple[str, ...]] = {
    "layout": ("macros", "netlist"),
    "macros": ("netlist",),
    "netlist": ("graph", "registers", "interface", "guards", "simulator"),
    "graph": ("registers", "interface", "guards", "simulator"),
}


@dataclass
class Design:
    """A GDS (or a netlist), and everything worked out from it

    Build one with `open` or `from_netlist` rather than by hand; the
    constructor takes the same arguments but does no source validation.
    """

    source: Path | bytes | None = None
    tech: Path | TechConfig | None = None
    top: str | None = None
    lef: Path | None = None

    # Memoisation is a plain dict on the instance. Not using `functools.lru_cache` on
    # the methods, as that holds `self` in a module-level cache for the life of the
    # process, which in a long-lived worker is effectively a leak
    _cache: dict[Any, Any] = field(default_factory=dict, repr=False, compare=False)
    # Kept alive, not cached: bytes sources are materialised into this and the
    # file is removed when the Design is dropped.
    _temp: Any = field(default=None, init=False, repr=False, compare=False)

    @classmethod
    def open(
        cls,
        source: Path | str | bytes,
        *,
        tech: Path | TechConfig | None = None,
        top: str | None = None,
        lef: Path | None = None,
    ) -> "Design":
        """A design from a GDS, from a netlist JSON, or from GDS bytes

        `bytes` is for the browser, which has an ArrayBuffer and no filesystem
        to put it on.
        """
        if isinstance(source, str):
            source = Path(source)
        return cls(source=source, tech=tech, top=top, lef=lef)

    @classmethod
    def from_netlist(cls, nl: Netlist) -> "Design":
        """A design from an already-extracted netlist, skipping extraction

        The browser uses this with a pre-baked netlist.json. There is no layout
        behind it, so `.layout` raises.
        """
        design = cls(source=None)
        design._cache["netlist"] = nl
        return design

    #! memoisation

    def _memo(self, key: Any, make: Callable[[], Any]) -> Any:
        # Not `setdefault`: that would evaluate `make()` on every hit, which is
        # the whole thing this exists to avoid.
        if key not in self._cache:
            self._cache[key] = make()
        return self._cache[key]

    def invalidate(self, *what: str) -> None:
        """Drop cached results, and everything derived from them

        With no arguments, drops everything. Names are the unparameterised
        forms -- `invalidate("interface")` drops the answer for every `cycles`.
        """
        if not what:
            self._cache.clear()
            return
        doomed: set[str] = set()
        queue = list(what)
        while queue:
            name = queue.pop()
            if name in doomed:
                continue
            doomed.add(name)
            queue.extend(_DEPENDENTS.get(name, ()))
        if "graph" in doomed and "netlist" in self._cache:
            # The netlist holds one too (`Graph.of`), and it would otherwise
            # hand the discarded one straight back.
            Graph.forget(self._cache["netlist"])
        self._cache = {
            key: value
            for key, value in self._cache.items()
            if (key if isinstance(key, str) else key[0]) not in doomed
        }

    #! the source

    def _path(self) -> Path:
        """The source as a path, writing `bytes` out to a temporary file once"""
        if isinstance(self.source, Path):
            return self.source
        if self._temp is None:
            self._temp = tempfile.NamedTemporaryFile(suffix=".gds")
            self._temp.write(self.source)
            self._temp.flush()
        return Path(self._temp.name)

    def _is_netlist_source(self) -> bool:
        return isinstance(self.source, Path) and self.source.suffix == ".json"

    @property
    def layout(self):
        """The opened GDS, a `loader.Design`

        Raises `NoLayoutAvailable` when there is not one.
        """
        if self.source is None:
            raise NoLayoutAvailable(
                "this design was built from a netlist; it has no layout"
            )
        if self._is_netlist_source():
            raise NoLayoutAvailable(f"{self.source} is a netlist, not a layout")
        return self._memo("layout", self._open_layout)

    def _open_layout(self):
        from .. import config, loader  # deferred: loader pulls in the geo backend

        tech = (
            self.tech
            if isinstance(self.tech, config.TechConfig)
            else config.load(self.tech)
        )
        return loader.load(self._path(), tech, self.top)

    def macros(self) -> dict:
        """The LEF abstracts, for a GDS that only references its cells"""
        if self.lef is None:
            return {}
        return self._memo("macros", self._read_macros)

    def _read_macros(self) -> dict:
        from .. import lef  # deferred: reads against the layout's dbu

        return lef.read(self.lef, self.layout.dbu)

    #! the netlist and its graph

    @property
    def netlist(self) -> Netlist:
        """The gate-level netlist, extracted on first use"""
        return self._memo("netlist", self._build_netlist)

    def _build_netlist(self) -> Netlist:
        if self._is_netlist_source():
            return Netlist.from_dict(json.loads(self.source.read_text()))
        from .. import netlist  # deferred: imports the extraction stack

        return netlist.build(self.layout, macros=self.macros())

    @property
    def graph(self) -> Graph:
        """The traversal API over this netlist, built once

        `Graph.of` keeps it on the netlist too, so an analysis that asks for a
        graph part-way down gets this one rather than building a second.
        """
        return self._memo("graph", lambda: Graph.of(self.netlist))

    #! memoised analyses

    def registers(self, *, ordered: bool = False) -> list["Register"]:
        """The recovered registers

        `ordered=True` additionally resolves bit order, which is what
        `gdsx registers` reports; the two are cached separately.
        """
        from .. import analyse  # deferred, as the other analyses are

        if ordered:
            return self._memo(
                ("registers", True),
                lambda: analyse.resolve_bit_order(self.netlist, self.registers()),
            )
        return self._memo(
            ("registers", False), lambda: analyse.find_registers(self.netlist)
        )

    def interface(self, cycles: int | None = None) -> "Interface":
        """What each port is for. Memoised per `cycles` value, not globally:
        a longer measurement is a different question, not a cache hit."""
        from .. import interface

        count = interface.CYCLES if cycles is None else cycles
        return self._memo(
            ("interface", count), lambda: interface.describe(self.netlist, count)
        )

    def guards(self, *, min_fanout: int | None = None) -> "Guards":
        """The freeze condition for each flop, memoised per `min_fanout`"""
        from .. import guards

        fanout = guards.MIN_FANOUT if min_fanout is None else min_fanout
        return self._memo(
            ("guards", fanout), lambda: guards.find(self.netlist, min_fanout=fanout)
        )

    def simulator(self) -> "Simulator":
        """A simulator over this netlist

        Memoised, and a `Simulator` carries state: successive callers share one
        instance and whatever the last of them left in it. Call `reset()` if
        that matters.
        """
        from ..sim import Simulator

        return self._memo("simulator", lambda: Simulator(self.netlist))
