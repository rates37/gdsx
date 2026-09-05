"""The binary render bundle for the browser die view.

The UI needs geometry a GPU can render -- flat `Int32Array`s -- not a
tree of Python objects, and it cannot draw `puzzle.gds`'s ~10^5
rectangles in one frame. So this module:

* decomposes routing-layer geometry into three LOD levels (L0 full detail,
  L1 small rects collapsed into their net's bounding box per layer, L2 none
  -- at that zoom only the `instances` array, which is LOD-independent,
  is worth drawing),
* buckets every rendered rectangle into a 16x16 spatial tile so the browser
  can upload only the tiles a pan/zoom actually needs,
* carries a net id on every rectangle (`net_of_shape`) and the reverse index
  (`net_shapes`), which is what makes clicking a net in the netlist browser
  highlight its metal.

Coordinates stay integer database units everywhere except one field: `dbu`,
the microns-per-database-unit conversion factor, in the header. That is the
only place a float appears -- the same discipline `geo/clusters.py` uses,
converting to microns only at the very last step before display.
"""

from __future__ import annotations

import json
import struct
from collections import defaultdict
from dataclasses import dataclass

from .connectivity import Connectivity
from .geo import clusters as geo_clusters
from .geo import types as g
from .loader import Design

#: Bumped whenever the bundle layout changes. Cached bundles invalidate on it.
#: 2 added the device-level layers and the `layer_kind` map.
SCHEMA_VERSION = 2

#: L0 full detail, L1 small-rect collapse, L2 cells only (no per-layer geometry).
LOD_LEVELS = 3

#: Below this area (database units squared) on L1, a rectangle is not drawn on
#: its own; instead its whole net's bounding box on that layer is drawn once.
#: Taken literally from the plan document ("rects below 4 dbu^2"). At sky130
#: scale (minimum widths run ~150-460 dbu) this merges very little -- if L1
#: needs to cut the rect count harder, raise this, but the value here matches
#: what was specified rather than guessing a bigger one.
DEFAULT_LOD1_MIN_AREA = 4

#: Spatial tile grid the die is bucketed into, per layer per LOD.
TILE_GRID = 16

Rect = tuple[int, int, int, int]


class _Blob:
    """Accumulates flat int32 arrays, handing back where each one landed"""

    def __init__(self) -> None:
        self._buf = bytearray()

    def add_i32(self, values: list[int], stride: int) -> dict:
        """Append `values` (already flat) as int32; `stride` ints per item"""
        offset = len(self._buf) // 4
        if values:
            self._buf += struct.pack(f"<{len(values)}i", *values)
        return {"offset": offset, "count": len(values) // stride, "stride": stride}

    def bytes(self) -> bytes:
        return bytes(self._buf)


@dataclass
class RenderBundle:
    """A render bundle: a JSON header describing offsets into a binary blob"""

    header: dict
    blob: bytes

    def pack(self) -> bytes:
        """One self-contained `bytes`: a uint32 header length, the UTF-8 JSON
        header, then the blob. This is what `api.render_bundle` returns.
        """
        header_bytes = json.dumps(self.header).encode("utf-8")
        return struct.pack("<I", len(header_bytes)) + header_bytes + self.blob

    @classmethod
    def unpack(cls, data: bytes) -> "RenderBundle":
        (n,) = struct.unpack_from("<I", data, 0)
        header = json.loads(bytes(data[4 : 4 + n]))
        return cls(header, bytes(data[4 + n :]))


def _net_at(conn: Connectivity, layer: str, rect: Rect) -> int | None:
    """The net id under one corner of `rect`

    Any point strictly inside a rectangle that came out of `rects_of` is
    inside the cluster it was decomposed from -- the corner is the cheapest
    such point, and inclusive bounds (`geo/clusters.py`) make it safe.
    """
    return conn.net_at(layer, g.Point(rect[0], rect[1]))


def _rect_area(rect: Rect) -> int:
    return (rect[2] - rect[0]) * (rect[3] - rect[1])


def _union(a: Rect, b: Rect) -> Rect:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _lod0(
    design: Design, conn: Connectivity, new_shape
) -> tuple[dict[str, list[tuple[Rect, int, int]]], dict[tuple[str, int], Rect]]:
    """Full-detail geometry per routing layer, plus each (layer, net)'s bbox

    The bbox is the exact bounding box of every rectangle assigned to that
    net on that layer, the same thing a `RectCluster` reports, worked out
    here instead of through the geometry backend so this pass does not care
    which backend built `conn`.
    """
    by_layer: dict[str, list[tuple[Rect, int, int]]] = {}
    cluster_bbox: dict[tuple[str, int], Rect] = {}

    for rl in design.tech.routing:
        idx = design.index_of(rl.drawing)
        items: list[tuple[Rect, int, int]] = []
        if idx is not None:
            for shape in design.layout.shapes_rec(design.top, idx):
                rects = geo_clusters.rects_of(shape)
                if not rects:
                    continue
                net_raw = _net_at(conn, rl.name, rects[0])
                sid = new_shape(net_raw)
                for rect in rects:
                    items.append((rect, sid, net_raw))
                    if net_raw is not None:
                        key = (rl.name, net_raw)
                        cluster_bbox[key] = (
                            _union(cluster_bbox[key], rect)
                            if key in cluster_bbox
                            else rect
                        )
        by_layer[rl.name] = items

    return by_layer, cluster_bbox


def _lod1(
    design: Design,
    lod0: dict[str, list[tuple[Rect, int, int]]],
    cluster_bbox: dict[tuple[str, int], Rect],
    new_shape,
    min_area: int,
) -> dict[str, list[tuple[Rect, int, int]]]:
    """L0, with every rect under `min_area` collapsed into its net's bbox

    Collapsing is per (layer, net): however many small rects a net has on one
    layer, they contribute one merged rectangle, not one each.
    """
    by_layer: dict[str, list[tuple[Rect, int, int]]] = {}
    for rl in design.tech.routing:
        merged_sid: dict[int, int] = {}
        out: list[tuple[Rect, int, int]] = []
        for rect, _sid, net_raw in lod0[rl.name]:
            if net_raw is None:
                continue
            if _rect_area(rect) >= min_area:
                out.append((rect, new_shape(net_raw), net_raw))
                continue
            if net_raw not in merged_sid:
                merged_sid[net_raw] = new_shape(net_raw)
                out.append((cluster_bbox[(rl.name, net_raw)], merged_sid[net_raw], net_raw))
        by_layer[rl.name] = out
    return by_layer


def _tile_index(rect: Rect, bbox: g.Box, cols: int, rows: int) -> int:
    """The tile a rectangle's centre falls in

    A centroid, not the rectangle's own extent: a large rectangle (an L1
    merged net bbox, say) would otherwise belong to every tile it touches.
    Documented approximation, same spirit as the round-path-as-square one in
    `geo/clusters.py` -- panning near a tile edge can occasionally miss a
    large shape that overlaps it without centring on it.
    """
    width = max(bbox.width(), 1)
    height = max(bbox.height(), 1)
    cx = (rect[0] + rect[2]) // 2
    cy = (rect[1] + rect[3]) // 2
    tx = min(cols - 1, max(0, (cx - bbox.left) * cols // width))
    ty = min(rows - 1, max(0, (cy - bbox.bottom) * rows // height))
    return ty * cols + tx


def _emit_layer(
    items: list[tuple[Rect, int, int]], bbox: g.Box, blob: _Blob, cols: int, rows: int
) -> dict:
    """One layer's rects, tiled and written into the blob"""
    keyed = sorted(items, key=lambda it: _tile_index(it[0], bbox, cols, rows))

    counts = [0] * (cols * rows)
    for rect, _sid, _net in keyed:
        counts[_tile_index(rect, bbox, cols, rows)] += 1
    offsets = [0] * (cols * rows + 1)
    for i in range(cols * rows):
        offsets[i + 1] = offsets[i] + counts[i]

    rect_flat: list[int] = []
    shape_flat: list[int] = []
    for rect, sid, _net in keyed:
        rect_flat.extend(rect)
        shape_flat.append(sid)

    return {
        "rects": blob.add_i32(rect_flat, stride=4),
        "shape_ids": blob.add_i32(shape_flat, stride=1),
        "tiles": blob.add_i32(offsets, stride=1),
    }


#: The synthetic layer under everything: one rectangle covering the die. There
#: is no GDS geometry for "the wafer", but every GDS viewer draws it, and
#: without it the lowest real layer floats over nothing.
SUBSTRATE = "substrate"


def _device(
    design: Design, new_shape
) -> dict[str, list[tuple[Rect, int, int | None]]]:
    """Device-level geometry: wells, diffusion, poly, contacts.

    These carry no net -- nothing is traced on them -- so every shape gets a
    net of `None` and is never highlighted. They are here because they are
    two thirds of what a chip looks like: `li1` sits ~0.94 um above the
    substrate, and with that space empty the metal reads as hovering rather
    than as the top of something.

    Device geometry is drawn inside the standard cells, so this walks the
    hierarchy the same way `_lod0` does.
    """
    by_layer: dict[str, list[tuple[Rect, int, int | None]]] = {}
    bbox = design.layout.cell_bbox(design.top)
    by_layer[SUBSTRATE] = [
        ((bbox.left, bbox.bottom, bbox.right, bbox.top), new_shape(None), None)
    ]
    for dl in getattr(design.tech, "device", ()):
        idx = design.index_of(dl.drawing)
        items: list[tuple[Rect, int, int | None]] = []
        if idx is not None:
            for shape in design.layout.shapes_rec(design.top, idx):
                for rect in geo_clusters.rects_of(shape):
                    items.append((rect, new_shape(None), None))
        by_layer[dl.name] = items
    return by_layer


def build(
    design: Design,
    conn: Connectivity,
    net_names: dict[int, str],
    *,
    lod1_min_area: int = DEFAULT_LOD1_MIN_AREA,
    tile_grid: int = TILE_GRID,
) -> RenderBundle:
    """The render bundle for `design`

    `conn` and `net_names` come from `netlist.trace_design` /
    `netlist.build_with_net_ids` -- callers that already have a `Netlist`
    should build both from the same `Connectivity` that produced it, so a
    shape's net id here means the same net the netlist browser shows.
    """
    tech = design.tech
    top_bbox = design.layout.cell_bbox(design.top)
    blob = _Blob()

    shape_net_raw: list[int | None] = []

    def new_shape(net_raw: int | None) -> int:
        sid = len(shape_net_raw)
        shape_net_raw.append(net_raw)
        return sid

    lod0, cluster_bbox = _lod0(design, conn, new_shape)
    lod1 = _lod1(design, lod0, cluster_bbox, new_shape, lod1_min_area)
    lod2: dict[str, list[tuple[Rect, int, int]]] = {rl.name: [] for rl in tech.routing}

    # Device layers are LOD-independent in kind but not in cost: `licon1` is
    # tens of thousands of sub-micron contacts, which are invisible at L1 and
    # would double the bundle for nothing. So L1 keeps only what is still
    # legible when zoomed out, by the same area rule the routing layers use,
    # and L2 keeps only the substrate -- the one shape that is the die.
    device0 = _device(design, new_shape)
    contacts = {d.name for d in getattr(tech, "device", ()) if d.via}
    device1 = {
        name: ([] if name in contacts else rects)
        for name, rects in device0.items()
    }
    device2 = {name: (device0[name] if name == SUBSTRATE else []) for name in device0}
    for level, extra in ((lod0, device0), (lod1, device1), (lod2, device2)):
        level.update(extra)

    # Bottom to top: substrate, the device layers in stack order, then the
    # routing layers. This is the draw order, the 2D pick order and the index
    # the 3D explode keys off, so it has to be the physical order.
    device_order = [SUBSTRATE] + [d.name for d in getattr(tech, "device", ())]
    stack_pos = {s.name: i for i, s in enumerate(tech.stack)}
    device_order.sort(key=lambda n: stack_pos.get(n, -1))
    layer_order = device_order + [rl.name for rl in tech.routing]

    lods = {}
    for level, layers in enumerate((lod0, lod1, lod2)):
        lods[str(level)] = {
            name: _emit_layer(layers[name], top_bbox, blob, tile_grid, tile_grid)
            for name in layer_order
        }

    # net numbering: dense 0..n-1 over every net id `conn` actually produced,
    # so array lengths below are compact regardless of the union-find's own
    # (sparse) root numbering.
    raw_roots = sorted(conn.nets)
    dense_of = {raw: i for i, raw in enumerate(raw_roots)}
    n_nets = len(raw_roots)
    net_names_out = {
        str(dense_of[raw]): net_names.get(raw, f"n{raw}") for raw in raw_roots
    }

    # Tracing finds every electrically distinct piece of metal; the netlist
    # keeps only the ones that reach a logic cell's pin. The rest -- power
    # stubs under fill and tap cells, stray routing that ends nowhere -- are
    # still drawn and still pickable, and their `n<id>` fallback name above
    # looks exactly like a real net's. Say plainly which ones they are, so a
    # viewer can offer "open this in the netlist" only where that will work.
    # (The fallback cannot collide with a real name: both are `n<raw id>`
    # over the same ids, so the same string always means the same net.)
    unextracted = [dense_of[raw] for raw in raw_roots if raw not in net_names]

    net_of_shape = [
        dense_of[raw] if raw is not None else -1 for raw in shape_net_raw
    ]
    by_net: dict[int, list[int]] = defaultdict(list)
    for sid, raw in enumerate(shape_net_raw):
        if raw is not None:
            by_net[dense_of[raw]].append(sid)
    net_shape_offsets = [0] * (n_nets + 1)
    net_shape_ids: list[int] = []
    for dense in range(n_nets):
        net_shape_offsets[dense] = len(net_shape_ids)
        net_shape_ids.extend(by_net.get(dense, ()))
    net_shape_offsets[n_nets] = len(net_shape_ids)

    # instances: every placed leaf cell, footprint + orientation + cell type,
    # so the die view can draw fill/tap/decap cells too, not just logic gates
    cell_names = sorted({name for name, _ in design.instances()})
    cell_id_of = {name: i for i, name in enumerate(cell_names)}
    placements = sorted(
        design.instances(), key=lambda t: (t[0], t[1].disp.y, t[1].disp.x, str(t[1]))
    )
    inst_flat: list[int] = []
    for cell_name, trans in placements:
        box = trans * design.layout.cell_bbox(cell_name)
        if box.empty():
            continue
        orient = trans.rot | (4 if trans.mirror else 0)
        inst_flat.extend(
            [box.left, box.bottom, box.right, box.top, orient, cell_id_of[cell_name]]
        )

    header = {
        "schema_version": SCHEMA_VERSION,
        "dbu": design.dbu,  # microns per database unit: the one float in this file
        "top": design.top,
        "bbox": [top_bbox.left, top_bbox.bottom, top_bbox.right, top_bbox.top],
        "stack": [
            {"name": s.name, "z": s.z, "thickness": s.thickness, "via": s.via}
            for s in tech.stack
        ],
        "tile_grid": {"cols": tile_grid, "rows": tile_grid},
        "layers": layer_order,
        # Which layers carry nets and which are scenery. A device layer is
        # never highlighted, never picked, and never part of a net.
        "layer_kind": {
            **{name: "device" for name in device_order},
            **{rl.name: "routing" for rl in tech.routing},
        },
        # Layers that cover regions rather than drawing wires, which the
        # viewer renders flatter so they read as ground.
        "fill_layers": [SUBSTRATE]
        + [d.name for d in getattr(tech, "device", ()) if d.fill],
        # Layers with no GDS geometry behind them, invented by this module.
        # The 3D view wants the substrate slab under everything; the 2D view
        # must not draw it, because from above it is an opaque rectangle the
        # size of the die and it would hide the whole design.
        "synthetic_layers": [SUBSTRATE],
        "lod_levels": LOD_LEVELS,
        "lod1_min_area": lod1_min_area,
        "lods": lods,
        "instances": blob.add_i32(inst_flat, stride=6),
        "cell_names": cell_names,
        "n_nets": n_nets,
        "net_names": net_names_out,
        # Dense ids with no counterpart in the netlist (see above). Sorted.
        "unextracted_nets": unextracted,
        "n_shapes": len(shape_net_raw),
        "net_of_shape": blob.add_i32(net_of_shape, stride=1),
        "net_shapes": {
            "offsets": blob.add_i32(net_shape_offsets, stride=1),
            "ids": blob.add_i32(net_shape_ids, stride=1),
        },
    }

    return RenderBundle(header=header, blob=blob.bytes())