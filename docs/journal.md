# Journal

Since I'm not super familiar with ASIC design, I want to abstract it asap. Will keep high level plan notes at the top of this doc and extend them as I go. Rest of the doc is just a rough journal of progress and design decisions.

- Map the GDS to low level tech agnostic netlist
- Then it can more easily be simulated and analysed (at least given my skillset), probably turns into a neat(ish) graph problem

## Exploring the Warmup GDS

Using KLayout to explore the warmup .gds. Has good python API , simple to add as a dependency.

scripts/01-check-insts.py loads the .gds, counts the number of each cell and reports them. Also prints the labels. Output from that script:

```
268 VIA_L1M1_PR_MR
313 VIA_M1M2_PR
45 VIA_M2M3_PR
5 VIA_M3M4_PR
75 VIA_via2_3_2000_480_1_6_320_320
75 VIA_via3_4_2000_480_1_5_400_400
75 VIA_via4_5_2000_480_1_5_400_400
13 VIA_via5_6_2000_2000_1_1_1600_1600
1 sky130_fd_sc_hd__a21bo_2
1 sky130_fd_sc_hd__a21boi_2
1 sky130_fd_sc_hd__a21o_2
5 sky130_fd_sc_hd__a31o_2
7 sky130_fd_sc_hd__and2_2
1 sky130_fd_sc_hd__and3_2
2 sky130_fd_sc_hd__and4bb_2
3 sky130_fd_sc_hd__clkbuf_16
58 sky130_fd_sc_hd__decap_3
16 sky130_fd_sc_hd__dfrtp_2
16 sky130_fd_sc_hd__mux2_1
4 sky130_fd_sc_hd__nand2_2
8 sky130_fd_sc_hd__nor2_2
1 sky130_fd_sc_hd__o21bai_2
5 sky130_fd_sc_hd__or2_2
93 sky130_fd_sc_hd__tapvpwrvgnd_1
3 sky130_fd_sc_hd__xnor2_2
5 sky130_fd_sc_hd__xor2_2
total 1099
TEXT layer 70 5 6 ['A', 'B', 'S', 'clk', 'en', 'rst_n']
TEXT layer 71 5 2 ['VGND', 'VPWR']
TEXT layer 72 5 2 ['VGND', 'VPWR']
```

I wasn't familiar with any of this stuff, but after a google, it seems like all the `sky130_fd_sc` cells are standard cells (logic gates, flip-flops, etc.), and all other cells are just physical implementation helpers like Vias. For this puzzle, they're probably not important. TODO: check for easter eggs here.

The `TEXT` layers are the labels for the top-level ports. Reads as expected (given the warmup gds came with the source verilog).

## GDSX Library: Config & Loader

Hoping to make this into a library of tools that can be used to understand the warmup (and ideally help with the real puzzle).

Added a config.yaml file for the sky130 layer stack, but can adapt to other PDK by directing at a different yaml. `src/gdsx/config.py` loads the config.yaml and provides a `Config` object. The config contains the layer stack, via pairs, and other information needed to trace the routing.

`src/gdsx/loader.py` contains:

- `Design` class, storing:
  - The KLayout `Layout` object
  - Reference to the top level cell
  - The `TechConfig` definitions
  - Mapping from `(layer, datatype)` to Klayout layer indices

- `load(path: Path | str, tech: TechConfig, top_name: str) -> Design` function to load a GDS and create a `Design` object.

- `Inspection` class, which stores various details of the design (top level, layers, cell counts, number of logic cells, number of non-logic cells, whether it's self contained, etc.)

- `inspect(design: Design) -> Inspection` function to create an `Inspection` object from a `Design` object. This accounts for nested cells in the gds and recursively walks them to return an accurate report

## Pins

A logic cell has pins like "input A" "output Y" etc. Need to find their coordinates in the GDS. Sky130 stores this as text labels. Each pin is a text string sitting on a metal layer positioned at the point it can connect.

Need to:

1. Know where to look
2. avoid looking inside cells (irrelevant to the top level netlist)
3. cache results for speed

`src/gdsx/pins.py` contains:

- A `Pin` data class to hold name, layer, and point.

- A `PinOrcale` caches pins by cell name

For now, the output pin names are hard coded. Want to come back to this eventually.

## Connected Metal Components

Metal routing is a connected component problem. Two metal shapes that touch are on the same net. Group all touching shapes into clusters.

It isn't necessaily just 1 layer. A signal might start on li1, go up through a via to met1, route on met1, go through via to met2, route on met2, etc.

Need to unify all these into single nets, and account for those vertical connections through vias.

`src/gdsx/connectivity.py` implements a core algorithm with three main parts:

### Per-layer merging:

KLayout's `Region.merged()` method does this for us. For each routing layer (li1, met1, met2, .....):

1. Get all shapes on the drawing layer (where the metal is)
2. Get all shapes on the pin layer (label points)
3. Merge them -> gives maximal connected regions

Each region gets a unique ID:

```py
for rl in design.tech.routing:
    region = (_region(design, rl.drawing) + _region(design, rl.pin)).merged()
    polys, ids = [], []
    for poly in region.each():
        polys.append(poly)
        ids.append(uf.add())  # Assign a cluster ID
    conn.index[rl.name] = PointIndex(polys, ids)
```

### Via stiching

Now we have per layer clusters, but need to now connect them through the vias. For each via instance:

1. Find the cluster it overlaps on the layer below
2. Find the cluster it overlaps on the layer above
3. Merge those two

Uses Union-Find data structure with path compression and union by size heuristic (amortised O(ackermann(n))).

```py
for via in design.tech.vias:
    for poly in _region(design, via.layer).merged().each():
        pt = poly.bbox().center()
        lo = conn.cluster_at(via.below, pt)
        hi = conn.cluster_at(via.above, pt)
        if lo is None or hi is None:
            conn.dangling_vias.append((via.name, pt))  # Error tracking
            continue
        uf.union(lo, hi)  # Merge clusters
```

### Spatial indexing for point queries

Now that we have clusters, the point of this was to be able to answer a query like "given (x,y) on layer L, what net is it on?"

Iterating through all clusters every query would get slow for large designs, so build a spatial index:

```py
class PointIndex:
    def __init__(self, polygons: list[db.Polygon], ids: list[int]):
        self.polygons = polygons
        self.ids = ids
        self.buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
        for i, poly in enumerate(polygons):
            bb = poly.bbox()
            for bx in range(bb.left // BUCKET, bb.right // BUCKET + 1):
                for by in range(bb.bottom // BUCKET, bb.top // BUCKET + 1):
                    self.buckets[(bx, by)].append(i)

    def lookup(self, pt: db.Point) -> int | None:
        for i in self.buckets.get((pt.x // BUCKET, pt.y // BUCKET), ()):
            poly = self.polygons[i]
            if poly.bbox().contains(pt) and poly.inside(pt):
                return self.ids[i]
        return None
```

Using `BUCKET = 5000` for a 5 micron grid size.

## Mapping Instance Pins to Nets

In the warmup gds, there were 79 gate instances in total. Need to determine which net each pin connects to.

In `src/gdsx/netlist.py`, the `build` function does the following:

For each logic cell instance:

1. Get its position and orientation

2. Query the `PinOracle` for name, layer, point

3. Transform the point to global coordinates

4. Query `Connectivity` object to find the net at that point

5. Record mapping form `pin_name` to `net_id`

Since pins can have multiple labels, they might land on different nets. So for each pin decide if:

- 0 nets: pin is floating
- 1 net: record it
- > 1 nets: conflict, mark is as an error but still use the first net found

## Emitting the Netlist

Now that we have a netlist, we can emit it in a human readable format. In `src/gdsx/netlist.py`, it supports exporting to JSON, Verilog, and Dot (graph visualisation).

JSON for easy parsing/reading, Verilog for possibly simulating, and Dot for visualising.

Sadly none of them really give any good idea of what the circuit does, even for the warmup gds. Not unexpected though. There's also the possibility of errors in my design.

Next I will try to simulate the netlist to see if it behaves as expected. Then fix whatever errors I made up to this point.

## CLI

Nvm, updated to add a cli interface for the library. Makes it easier to move forward when adding new features.

Inspecting:

```bash
$ uv run gdsx inspect samples/sample.gds
adder_demo  dbu=0.001  layers=38
pin source: in-GDS labels
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━┓
┃ kind                       ┃ cells ┃ instances ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━┩
│ logic                      │ 16    │ 79        │
│ non-logic (tap/decap/fill) │ 2     │ 151       │
│ other (via/unknown)        │ 8     │ 869       │
└────────────────────────────┴───────┴───────────┘

logic cells
    16 x sky130_fd_sc_hd__mux2_1
    16 x sky130_fd_sc_hd__dfrtp_2
     8 x sky130_fd_sc_hd__nor2_2
     7 x sky130_fd_sc_hd__and2_2
     5 x sky130_fd_sc_hd__a31o_2
     5 x sky130_fd_sc_hd__xor2_2
     5 x sky130_fd_sc_hd__or2_2
     4 x sky130_fd_sc_hd__nand2_2
     3 x sky130_fd_sc_hd__clkbuf_16
     3 x sky130_fd_sc_hd__xnor2_2
     2 x sky130_fd_sc_hd__and4bb_2
     1 x sky130_fd_sc_hd__and3_2
     1 x sky130_fd_sc_hd__a21boi_2
     1 x sky130_fd_sc_hd__o21bai_2
     1 x sky130_fd_sc_hd__a21bo_2
     1 x sky130_fd_sc_hd__a21o_2

top-level labels
  A  (met3)
  B  (met3)
  S  (met3)
  VGND  (met4)
  VGND  (met5)
  VPWR  (met4)
  VPWR  (met5)
  clk  (met3)
  en  (met3)
  rst_n  (met3)
```

Extracting:

```bash
$ uv run gdsx extract samples/sample.gds
79 instances, 86 nets (1547 clusters merged by 335 nets)
ports: A (input), B (input), S (output), clk (input), en (input), rst_n (input)
  wrote out/adder_demo.json
  wrote out/adder_demo.v
  wrote out/adder_demo.dot
```

Cleaned up formatting and styling of output using Claude Haiku, I'm proudly not a design expert.
