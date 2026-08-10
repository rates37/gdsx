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

Committed up to here at ca28cab

## Simulating netlist

I mentioned before that I wanted to simulate the verilog, but instead I think I'll simulate in Python. I already have the netlist, and the functions of each gate are all trivial, so no need to drag in a Verilator or iverilog dependency.

In `src/gdsx/functions.py`, made a lookup table of cell behaviours, with a subset of the sky130 hd cells([link](https://sky130-unofficial.readthedocs.io/en/latest/contents/libraries/sky130_fd_sc_hd/README.html)) . Again this is hard-coded to the sky130 conventions, and is something I want to come back to later.

The cell names are stripped of library prefix and drive variant. So `sky130_fd_sc_hd__nand2_2` becomes `nand2` (that's all you need for the simulator, and likely all I need for solving the puzzle).

Then in `src/gdsx/sim.py`, the `Simulator` class can use these to simulate the circuit. Not going into much detail here, as I've done a lot of work on this sort of thing in another project for simulating the XC2064 FPGA, and the ideas are very similar, so don't need to remind myself of the details as much as the other sections.

```py
class Simulator:
    # some detail omitted..

    def settle(self, inputs: dict[str, int]) -> dict[str, int]:
        values = {}
        for inst, fn in self.combinational:
            # Look up the gate's behavior, evaluate its inputs, set its output
            pins = {p: values[inst.connections[p]] for p in fn.inputs}
            values[inst.connections[fn.output]] = fn.evaluate(pins)
        return values

    def step(self, inputs: dict[str, int]) -> dict[str, int]:
        values = self.settle(inputs)
        # Flops sample simultaneously
        nxt = {}
        for inst, fn in self.flops:
            nxt[inst.name] = values[inst.connections[fn.data]]
        self.state = nxt
        return self.settle(inputs)
```

Choosing to ignore undriven inputs (treated as 0 by the simulator, but floating in reality).

### Combinational Loops / Dependency Order

In simulation, the evaluation order of gates needs to be done in topological order. Otherwise results may be wrong, or gates need to be re-evaluated multiple times. Also raises the issue of combinational loops, which should not occur in a well designed circuit.

I'm assuming the puzzle circuit was synthesised from real, legal Verilog, meaning I assume no combinational loops exist. But still check for them since it's trivial from within the topological sort function:

```py
def _topological(gates, sources: set[str]):
    """Order combinational gates so every gate runs after its drivers."""
    pending = list(gates)
    known = set(sources)
    ordered = []
    while pending:
        ready = [g for g in pending if all(g[0].connections[p] in known for p in g[1].inputs)]
        if not ready:
            stuck = ", ".join(f"{i.name}" for i, _ in pending[:5])
            raise ValueError(f"combinational loop or undriven input near: {stuck}")
        for gate in ready:
            known.add(gate[0].connections[gate[1].output])
        ordered += ready
        pending = [g for g in pending if g not in ready]
    return ordered
```

## Finding Registers

The warmup design has 79 'gates', and 16 of those are flip flops. One step toward understanding the design is to group registers together. Since otherwise there would effectively be one massive register with wildly intertwined wiring to other logic within the circuit.

For now, I'm over-adapting the code to the warmup design since we know what it is. Later should be able to generalise it.

in the warmup design, the flips flops shift together to form two 8-bit shift registers. Following data dependencies (in `src/gdsx/analyse.py`):

```py
def find_registers(nl: Netlist) -> list[Register]:
    flops = [i for i in nl.instances if is_sequential(i.cell)]

    # For each flop, what feeds its D input:
    feeder: dict[str, str | None] = {}
    for f in flops:
        fn = lookup(f.cell)
        deps = support(nl, f.connections[fn.data])
        # Exclude self-loops (enable/hold paths)
        upstream = (deps & names) - {f.name}
        feeder[f.name] = next(iter(upstream)) if len(upstream) == 1 else None

    # Build chains: if A feeds B and B feeds C, that's one 3-bit shift register
    followers: dict[str, list[str]] = {}
    for name, src in feeder.items():
        if src:
            followers.setdefault(src, []).append(name)

    heads = [f.name for f in flops if feeder[f.name] is None]
    registers = []
    for head in heads:
        chain = [head]
        while len(followers.get(chain[-1], [])) == 1:
            chain.append(followers[chain[-1]][0])
        registers.append(Register(name=..., flops=chain))
    return registers
```

The `support()` function in analyse.py traces backwards through gates but stops at flip flop outputs. So only look at flip-flop-to-flip-flop chains, not combinational logic.

### Checking what register contents cause output to go high

Once grouped registers together, we can check what value in the register contents causes the output to go high. This is just brute force, likely not super helpful for the real puzzle (I don't even know if the real puzzle uses a shift-register-based input capturing). But it's a start for the warmup at least.

In simplified terms:

```py
def sweep(simulator: Simulator, registers: list[Register], output: str, inputs):
    """Every combination of register values -> the output bit it produces."""
    widths = [r.width for r in registers]
    for values in product(*(range(1 << w) for w in widths)):
        for reg, value in zip(registers, values):
            load_state(simulator, reg, value)
        yield values, simulator.settle(inputs)[output]
```

Then we can check if it matches a known pattern. For now, it's just addition and subtraction. Simplified again:

```py
def identify(simulator: Simulator, registers, output: str, inputs):
    truth = dict(sweep(simulator, registers, output, inputs))
    hits = {k for k, v in truth.items() if v == 1}

    # Is this output true when register_a + register_b equals some constant?
    if len(registers) == 2:
        sums = {a + b for a, b in hits}
        if len(sums) == 1:
            k = sums.pop()
            expected = {(a, b) for a, b in truth if a + b == k}
            if hits == expected:
                return f"{output} = ({registers[0].name} + {registers[1].name} == {k})"
        # same for subtraction...
```

For the sample: register values that produce output S=1 are those where A + B == 496. The code doesn't know 496 in advance, it dynamically discovers this by looking at which register states light up the output.

```py
from gdsx import config, loader, netlist, analyse

nl = netlist.build(loader.load("samples/sample.gds", config.load()))
a = analyse.analyse(nl)
for r in a.registers:
    print(r.name, r.width, r.serial_input, r.flops)
print(a.operators, a.notes)
```

The output:

```
reg_A 8 A ['dfrtp_2_12', 'dfrtp_2_10', 'dfrtp_2_15', 'dfrtp_2_11', 'dfrtp_2_14', 'dfrtp_2_13', 'dfrtp_2_16', 'dfrtp_2_9']
reg_B 8 B ['dfrtp_2_2', 'dfrtp_2_5', 'dfrtp_2_4', 'dfrtp_2_6', 'dfrtp_2_1', 'dfrtp_2_8', 'dfrtp_2_3', 'dfrtp_2_7']
['S = (reg_A + reg_B == 496)'] []
```

Here the bit ordering in the register chain can be identified. As if they were read backward, then different numbers would cause the output to go high, whereas with this ordering, the SAME sum causes the output to go high.

Again noting (and hitting myself in the head) that this is Extremely overfit to the warmup puzzle. But I guess (hope) that the real puzzle might use something similar in some way, so hopefully this effort isn't a red herring.

This brute force works here because there's only 16 registers -> 65536 combinations to test. The real puzzle will probably have a number of registers that makes this infeasible.

For now, it's a neat little set of functions to work perfectly with the sample, and probably completely meaningless for everything else:

```
$ uv run gdsx analyse samples/sample.gds
registers
  reg_A: 8-bit shift register <- A
    dfrtp_2_12 -> dfrtp_2_10 -> dfrtp_2_15 -> dfrtp_2_11 -> dfrtp_2_14 -> dfrtp_2_13 -> dfrtp_2_16 -> dfrtp_2_9
  reg_B: 8-bit shift register <- B
    dfrtp_2_2 -> dfrtp_2_5 -> dfrtp_2_4 -> dfrtp_2_6 -> dfrtp_2_1 -> dfrtp_2_8 -> dfrtp_2_3 -> dfrtp_2_7

operators
  S = (reg_A + reg_B == 496)
```

Listing limitations to come back to later (either to laugh at or to fix):

- only works with shift registers
- small input space (much past 20 bit registers and brute force is too slow)
- only supports minimal, known operators (add/sub)
- no FSM support

Committed up to here at c6fb802

## Verification

Next I want to verify that the netlist I extracted from the GDS is the same as the original design. `yosys` has good equivalence checking support, so I'll use that.

### Primitives

Before anything, we need implement the primitives. Instead of implementing primitives for the sky130 cells, I created functions to generate the abstract/general primitives using the existing function definitions from `src/gdsx/functions.py`. This way the primitives are automatically generated and consistent with the simulator.

Using the lambdas in the lookup tables from `functions.py`, I can generate a verilog expression to create the sum of minterms equation:

```py
def _minterms(fn: CellFunction) -> str:
    terms = []
    for combo in product((0, 1), repeat=len(fn.inputs)):
        values = dict(zip(fn.inputs, combo))
        if fn.evaluate(values):
            terms.append("(" + " & ".join(f"{'' if v else '~'}{p}" for p, v in values.items()) + ")")
    if not terms:
        return "1'b0"
    if len(terms) == 1 << len(fn.inputs):
        return "1'b1"
    return " | ".join(terms)  # Sum of minterms
```

Then generate the verilog module text:

```py
def _combinational_module(name: str, fn: CellFunction) -> str:
    ports = ", ".join(fn.inputs)
    return (
        f"module {name} ({ports}, {fn.output});\n"
        f"  input {ports};\n"
        f"  output {fn.output};\n"
        f"  assign {fn.output} = {_minterms(fn)};\n"
        f"endmodule\n"
    )
```

Similar approach for sequential cells too^

This approach is a bit janky, since it generates a bunch of basically trivial modules. But it ensures the simulator and the equivalence checking are using the same function (lambda) definitions.

### Verify

`src/gdsx/verify.py` bridges yosys and the library. The high level process is:

- Read the reference RTL
- Read the extracted netlist
- Create a "miter" circuit that asserts the two always agree
- Asks the SAT solver to find a counterexample. If it finds one, the two circuits are not equivalent. If it can't find one, they are equivalent.

### CLI

Then added CLI support for verification:

```bash
$ uv run gdsx verify samples/sample.gds --ref samples/sample.v -o out
proving out/adder_demo.generic.v == samples/sample.v ...
equivalent -- Induction step proven: SUCCESS!
```

As of right now, all prior mentioned limitations are still present.

Committed at 996911b

Added some even more heavily overfitted functions to identify adder and comparator. Useless for the real puzzle but good coding exercise.

## More planning

Currently the library is painfully over fit to the warmup. So before doing more stuff, I'm just going to lay out the plan to generalise / where to go from here.

### (A subset of) Current Issues

- Cell functions are hand-written in `src/gdsx/functions.py`, and only a small subset of the sky130 library is implemented

- `CellFunction` assumes one output, so `fa`/`ha`/`dfbbn`/tri-state cant be represented. I don't know if the real puzzle uses any of these

- Pin direction comes from naming convention

- `find_registers` only recognises shift register chains

- Analysis just enumerates over 2^20 register states, errors if more than 20 registers

- Most of the datapath analysis is overfit, useless in general

## Getting Liberty Data

google/skywater-pdk-libs-sky130_fd_sc_hd on GitHub publishes the data converted to json, one file per cell per drive strength.

Added `tools/fetch_liberty.py` to list the repo tree, pick the lowest drive strength of each cell, pull the file for each, and records the pin directions, output pin functions, and the ff/latch group (for sequential cells). Everything else discarded, as it's not relevant here.

Combined all into `config/sky130_fd_sc_hd.cells.json` for easy use.

### Parsing Boolean Expressoins

The liberty function string is something like "(A1&A2)|B1". Added a parser in `src/gdsx/liberty.py` to parse this. Liberty's syntax: ! or trailing ' is NOT, & or \* or juxtaposition is AND, ^ is XOR, | or + is OR, and 1/0 are constants.

Using a very minimal AST:

```py
Expr = tuple  # ("var", name) | ("not", e) | ("and"|"or"|"xor", a, b) | ("const", 0|1)
```

`evaluate(expr: Expr, values: dict[str, int]) -> int` evaluates the expression directly, and `to_verilog(expr: Expr) -> str` converts it to a verilog expression. This is used to generate a more reliable verilog primitive library.

Added tests for this in `tests/test_liberty.py`. Kept the old hand-written lookup table of functions in `tests/hand_functions.py`, and to keep the old tests working. Can be removed eventually.

### Cells with multiple outputs

Another problem from before was that the old cell data class only had a single output. That's fine for simple gates, but there are some more complex sky130 cells like a full adder has SUM and COUT, and dfbbn gives you both Q and Q_N off the same flip flop.

So moved to the following `Cell` data class:

```py
@dataclass(frozen=True)
class Cell:
    name: str  # base name, e.g. "nand2"
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    power: tuple[str, ...]
    functions: dict[str, Expr]  # output pin -> expression
    sequential: Sequential | None = None
    tristate: bool = False

    def evaluate(self, values: dict[str, int]) -> dict[str, int]:
        return {pin: evaluate(expr, values) for pin, expr in self.functions.items()}
```

This was annoying to refactor because it was already used in a lot of places. Also involved updating the simulator accordingly, as well as the `src/gdsx/analyse.py` too.

### Sequential Elements

The old `FlopFunction` hardcoded pin roles. Liberty already describes teh clocked_on and next_state as expressions, and this gives more general/flexible sequential elements for free.

```py
@dataclass(frozen=True)
class Sequential:
    state_vars: tuple[str, ...]
    clocked_on: Expr
    next_state: Expr
    clear: Expr | None = None
    preset: Expr | None = None
    is_latch: bool = False
```

This meant the simulator needs to also detect a clock edge, not just the clock net value.

## Analysing More Types of Registers

The old `find_registers` only looked for linearly chained shift registers. Mainly because it was the structure of the warm up puzzle. Now I'm looking past the warm up, so the real puzzle (almost) certainly won't have something like this. So identifying other common register constructs will probably be useful.

### Grouping

The new approach splits into a few steps:

1. Survey all flip flops. For each one, identify what controls it (cell type, clock net, async reset/set), and what it depends on (other FFs and top-level ports reachable through its next state logic).

2. Group by signature. Same clock and reset gives a candidate grouping. The warm up has two separate 8-bit shift registers but they would be in the same candidate group here. So this isn't enough to define a grouping. `_components()` does a walk of the graph over the `depends` relation to split a group into the sub-groups that actually exchange data:

```py
def _components(members: list[str], info: dict[str, _FlopInfo]) -> list[list[str]]:
    inside = set(members)
    # build undirected graph of dependencies
    adjacency = {m: (info[m].depends & inside) - {m} for m in members}
    for m, linked in list(adjacency.items()):
        for other in linked:
            adjacency[other] = adjacency[other] | {m}  # treat as undirected

    seen: set[str] = set()
    groups = []
    # find connected components
    for start in sorted(members):
        if start in seen:
            continue
        stack, group = [start], []
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            group.append(node)
            stack.extend(sorted(adjacency[node] - seen))
        groups.append(sorted(group))
    return groups
```

But for parallel loads, it would return 8 single registers, so this can be handled by treating all as a single parallel register.

3. Order the bits by the dependency depth. In simple words "how many other FFs in this group does this FF transitively depend on?"

4. Classify the shape, so we can report something more meaningful than "register"

```py
def _classify(group: list[str], info: dict[str, _FlopInfo]) -> str:
    inside = set(group)
    forward = {f: (info[f].depends & inside) - {f} for f in group}
    if len(group) > 1 and not any(forward.values()):
        return "parallel register"  # bits never cross depend, only shared control relates them
    if all(len(v) <= 1 for v in forward.values()):
        return "shift register"
    if any(f in info[f].depends for f in group):
        return "feedback register"  # counter, accumulator, LFSR, etc.
    return "register"
```

To test all this I built new circuits since the warm up was too simple(only had shift regs), and the real puzzle is too messy and complex (and I don't know what it is yet). Used AI to generate tests quickly and re-format. It's risky, but it's good enough for now so I can move forward.

Even now, attempting to read the sample, does not yield much more insight:

```
$ uv run gdsx analyse samples/puzzle.gds

blocks (functional match, with the gates backing each call)
  reg_dfrtp_2_11: 1-bit shift register  (153 cells)
  reg_dfrtp_2_12: 1-bit shift register  (155 cells)
  reg_dfrtp_2_13: 2-bit shift register, bit order unknown  (155 cells)
  reg_dfrtp_2_14: 2-bit shift register, bit order unknown  (157 cells)
  reg_dfrtp_2_19: 1-bit shift register  (153 cells)
  reg_dfrtp_2_21: 3-bit feedback register, bit order unknown  (16 cells)
  reg_dfrtp_2_22: 2-bit shift register, bit order unknown  (155 cells)
  reg_dfrtp_2_24: 1-bit shift register  (155 cells)
  reg_dfrtp_2_28: 2-bit shift register, bit order unknown  (155 cells)
  reg_dfrtp_2_3: 5-bit feedback register  (21 cells)
  reg_dfrtp_2_30: 2-bit shift register, bit order unknown  (155 cells)
  reg_dfrtp_2_32: 2-bit shift register, bit order unknown  (157 cells)
  reg_dfrtp_2_36: 2-bit shift register, bit order unknown  (155 cells)
  reg_dfrtp_2_44: 3-bit feedback register  (13 cells)
  reg_dfrtp_2_46: 1-bit shift register  (152 cells)
  reg_dfrtp_2_61: 6-bit feedback register, bit order unknown  (169 cells)
  reg_dfrtp_2_62: 2-bit shift register, bit order unknown  (10 cells)
  reg_dfrtp_2_64: 1-bit shift register  (6 cells)
  reg_dfrtp_2_65: 1-bit shift register  (8 cells)
  reg_dfrtp_2_66: 2-bit shift register, bit order unknown  (8 cells)
  reg_dfrtp_2_68: 2-bit shift register, bit order unknown  (10 cells)
  reg_dfrtp_2_7: 2-bit shift register, bit order unknown  (155 cells)
  reg_dfrtp_2_70: 1-bit shift register  (6 cells)
  reg_dfrtp_2_71: 5-bit parallel register, bit order unknown  (24 cells)
  reg_dfrtp_2_72: 1-bit shift register  (5 cells)
  reg_dfrtp_2_75: 1-bit shift register  (5 cells)
  reg_dfrtp_2_76: 2-bit shift register, bit order unknown  (8 cells)
  reg_dfrtp_2_80: 1-bit shift register  (6 cells)
  reg_dfrtp_2_81: 1-bit shift register  (8 cells)
  reg_dfrtp_2_82: 3-bit shift register, bit order unknown  (53 cells)
  reg_dfrtp_2_9: 3-bit feedback register  (14 cells)
  reg_dfstp_2_4: 1-bit shift register  (14 cells)
  reg_dfxtp_2_1: 2-bit shift register, bit order unknown  (7 cells)
  reg_dfxtp_2_3: 2-bit shift register, bit order unknown  (6 cells)
  reg_enable: 3-bit feedback register, bit order unknown  (27 cells)
  reg_enable: 1-bit shift register  (12 cells)
  reg_enable: 1-bit shift register  (3 cells)
  reg_enable: 1-bit shift register  (3 cells)
  reg_enable: 3-bit shift register  (7 cells)
  reg_enable: 1-bit shift register  (3 cells)
  reg_enable: 4-bit feedback register, bit order unknown  (15 cells)
  reg_enable: 1-bit shift register  (3 cells)
  reg_enable: 1-bit shift register  (3 cells)
  reg_enable: 3-bit feedback register, bit order unknown  (15 cells)
  reg_enable: 1-bit shift register  (7 cells)
  reg_enable: 3-bit feedback register, bit order unknown  (27 cells)
  clock_tree: buffers driving the flop clocks  (17 cells)
```

## Finding Buses

Before `split_datapath` was looking for a sum, since that's what the warm up was. Now I want to look for more general stuff.

The old implementation was roughly:

1. exhaustively sweep every register-state combination
2. check whether some net matches bit k of sum(a,b) for each k
3. if yes, return that as the adder

### Using random vectors as cheaper filter, exhaustively sweep to prove a hypothesis

The new implementation runs the design on a few hundred random register-state combinations instead of every single one:

```py
def sampled_vectors(
    simulator: Simulator,
    registers: list[Register],
    inputs: dict[str, int],
    count: int = 256,
    seed: int = 0,
):
    """Every net's behaviour over random register values.

    This is the filter: a few hundred vectors reject essentially every
    hypothesis that is wrong, and unlike the exhaustive sweep the cost
    doesn't depend on register width.
    """
    rng = random.Random(seed)
    combos = [tuple(rng.randrange(1 << r.width) for r in registers) for _ in range(count)]
    return combos, _probe(simulator, registers, inputs, combos)
```

256 random vectors "probably" rejects all wrong operator hypotheses. Ie., a net that happens to be equal to `a&b` at 256 separate random trials but isn't actually `a & b` would be quite unlikely.

If the design is small, the surviving hypotheses can be checked exhaustively. Above 20 registers (arbitrary choice for this task), the survivors are labelled as "sampled" to mark them as possible but not confirmed.

Instead of just sum, we can add more operators easily:

```py
OPERATORS = {
    "sum": ("+", lambda values: sum(values)),
    "difference": ("-", lambda values: values[0] - values[1]),
    "and": ("&", lambda values: values[0] & values[1]),
    "or": ("|", lambda values: values[0] | values[1]),
    "xor": ("^", lambda values: values[0] ^ values[1]),
}
```

`find_buses` tries each one and keeps whatever's found. Simplified:

```py
for name, (symbol, compute) in OPERATORS.items():
    description = f"{registers[0].name} {symbol} {registers[1].name}"
    for width in range(operand_width + 1, floor - 1, -1):
        top = {(compute(values) >> (width - 1)) & 1 for values in combos}
        if len(top) == 1:
            continue  # constant top bit: the result is narrower
        bus = find_bus(combos, vectors, compute, width, name, description)
        ...
```

Again, this isn't super helpful on its own but I hope it might get me one step closer to having a useful tool that can help deconstruct the real puzzle.

## SAT Proving Buses

From the previous section, where bit widths above 20 bits were labelled as "sampled" and not exhaustive confirmed. It's useful but not definitive, so has the chance to lead down red herrings.

To prove something stateless like `a + b`, it's just combinational logic, and SAT solvers can be used to prove that the logic is equivalent to the operator without exhaustive simulation. If the SAT solver can't find a counterexample, then the two are equivalent

### Cutting a cone out into its own module

Yosys has a SAT solver, but needs a full verilog module, not a subsection of the netlist. So I added a `to_cone_verilog` module to emit part of the netlist as a stand alone module:

```py
def to_cone_verilog(
    nl: Netlist,
    name: str,
    instances: set[str],
    rename: dict[str, str],
    outputs: list[str],
) -> str:
    """Emit part of the netlist as a standalone module, for proving in isolation
    """
    chosen = [inst for inst in nl.instances if inst.name in instances]
    driven, read = set(), set()
    for inst in chosen:
        cell = lookup(inst.cell)
        if cell is None:
            continue
        driven |= {inst.connections[p] for p in cell.functions if p in inst.connections}
        read |= {inst.connections[p] for p in cell.inputs if p in inst.connections}

    port = lambda net: rename.get(net, net)
    inputs = sorted(port(n) for n in read - driven - nl.power_nets)
    out_ports = [port(n) for n in outputs]
    internal = sorted({port(n) for n in driven} - set(out_ports))

    # generate verilog text..
```

`rename` used because gate level names (aleady generated by this library) are useless/meaningless, so instead rename them to match a hand written reference module.

### Generate reference module

Yosys needs a golden model to prove equivalence against. It's similarly trivial to generate, see `analyse.py::_reference()`. It's limited to common operators like +, -, &, etc. But is a start.

### Yosys Script

The pre-existing yosys equivalence script is close, but for combinational, it can be simplified to one SAT query, no induction needed:

```py
COMBINATIONAL_SCRIPT = """\
read_verilog {reference}
prep -top {ref_top} -flatten
design -stash gold

read_verilog {primitives} {extracted}
prep -top {gate_top} -flatten
design -stash gate

design -copy-from gold -as gold {ref_top}
design -copy-from gate -as gate {gate_top}
miter -equiv -flatten -make_assert gold gate miter
hierarchy -top miter
sat -verify -prove-asserts miter
"""
```

Refactored/integrated in with the existing verify components.

Added `gdsx analyse` to show how it knows something (SAT, exhaustive, sampled). Again cleaned up a lot with Claude.

## IDEA

Will test later after I finish this analytical path. Since module has 1-bit input, there's couod be some input hardwaer like a UART or something. This would involve periodic counting. (e.g., uart would count start bit, 8 data bits, stop bit, potional parity bit, but basically a counter would be needed to know when to sample/process input).

Toggle clock for X clock cycles, monitor all register contents to see periodicity?

## Lifting RTL

So far everything was kept in internal representations, but in the end I kind of want to re-generate the verilog source (or something equivalent, and readable), as I think this would provide a level of certainty of what the circuit does. Netlist is obviously too low level. So in `src/gdsx/lift.py`, added some functionality.

Since obviously I haven't recovered an actual high level internal representation, the output at this point will be mixed and mostly low level, aside from what has been proven. Always blocks for registers, continuous assignments for buses and predicates, and unaccounted gates are left as the generic cell instantiation.

Partial recovery isn't great but again, it can give some indication of the purpose.

See output in `out/adder_demo.rtl.v`. It looks especially good here because the library is already overfit to the warmup puzzle.

## FSMs

It's possible that the real puzzle has an FSM. Yosys has some FSM tools, but those look for RTL level structure, but when converted to primitives, the structure is lost so it's not that helpful.

The idea used here is essentially: load a register with a state, evaluate every next-state for every input combination, and BFS the reachable states from the initial(reset) state.

If a register can reach all values, it's likely data. But an FSMwith a 3-bit encoding that only ever visited 4/8 possible states is likely an FSM.

Thresholding on the ratio of reachable states to total states, we can guess it as an FSM.

This correctly doesn't find any FSMs for the warm up puzzle, since there isn't really any. But it does find some stuff in the real puzzle:

```
$ uv run gdsx fsm samples/puzzle.gds
4-state machine in reg_dfrtp_2_21 (3 bits, 50% of the encoding space used)
  inputs: I, enable

  state    encoding outputs                  transitions
  S0 (reset) 000    O[0]=0, O[1]=0, O[2]=0, O[3]=0, O[4]=0, O[5]=0, O[6]=0, O[7]=0, success=0 00->S0 01->S0 10->S0 11->S1
  S1       010    O[0]=0, O[1]=0, O[2]=0, O[3]=0, O[4]=0, O[5]=0, O[6]=0, O[7]=0, success=0 00->S1 01->S1 10->S1 11->S2
  S2       001    O[0]=0, O[1]=0, O[2]=0, O[3]=0, O[4]=0, O[5]=0, O[6]=0, O[7]=0, success=0 00->S2 01->S2 10->S2 11->S3
  S3       011    O[0]=0, O[1]=0, O[2]=0, O[3]=0, O[4]=0, O[5]=0, O[6]=0, O[7]=0, success=0 00->S3 01->S3 10->S3 11->S3

7-state machine in reg_dfrtp_2_61 (6 bits, 11% of the encoding space used)
  inputs: I, enable

  state    encoding  outputs                  transitions
  S0 (reset) 000000    O[0]=0, O[1]=0, O[2]=0, O[3]=0, O[4]=0, O[5]=0, O[6]=0, O[7]=0, success=0 00->S0 01->S0 10->S0 11->S1
  S1       100100    O[0]=0, O[1]=0, O[2]=0, O[3]=0, O[4]=0, O[5]=0, O[6]=0, O[7]=0, success=0 00->S1 01->S2 10->S1 11->S3
  S2       100000    O[0]=0, O[1]=0, O[2]=0, O[3]=0, O[4]=0, O[5]=0, O[6]=0, O[7]=0, success=0 00->S2 01->S2 10->S2 11->S3
  S3       010100    O[0]=0, O[1]=0, O[2]=0, O[3]=0, O[4]=0, O[5]=0, O[6]=0, O[7]=0, success=0 00->S3 01->S4 10->S3 11->S5
  S4       010000    O[0]=0, O[1]=0, O[2]=0, O[3]=0, O[4]=0, O[5]=0, O[6]=0, O[7]=0, success=0 00->S4 01->S4 10->S4 11->S5
  S5       110100    O[0]=0, O[1]=0, O[2]=0, O[3]=0, O[4]=0, O[5]=0, O[6]=0, O[7]=0, success=0 00->S5 01->S6 10->S5 11->S5
  S6       110000    O[0]=0, O[1]=0, O[2]=0, O[3]=0, O[4]=0, O[5]=0, O[6]=0, O[7]=0, success=0 00->S6 01->S6 10->S6 11->S5

4-state machine in reg_dfrtp_2_9 (3 bits, 50% of the encoding space used)
  inputs: I, enable

  state    encoding outputs                  transitions
  S0 (reset) 000    O[0]=0, O[1]=0, O[2]=0, O[3]=0, O[4]=0, O[5]=0, O[6]=0, O[7]=0, success=0 00->S0 01->S0 10->S0 11->S1
  S1       001    O[0]=0, O[1]=0, O[2]=0, O[3]=0, O[4]=0, O[5]=0, O[6]=0, O[7]=0, success=0 00->S1 01->S1 10->S1 11->S2
  S2       010    O[0]=0, O[1]=0, O[2]=0, O[3]=0, O[4]=0, O[5]=0, O[6]=0, O[7]=0, success=0 00->S2 01->S2 10->S2 11->S3
  S3       011    O[0]=0, O[1]=0, O[2]=0, O[3]=0, O[4]=0, O[5]=0, O[6]=0, O[7]=0, success=0 00->S3 01->S3 10->S3 11->S0

11-state machine in reg_enable (4 bits, 69% of the encoding space used)
  inputs: enable

  state    encoding outputs                  transitions
  S0 (reset) 0000    O[0]=0, O[1]=0, O[2]=0, O[3]=0, O[4]=0, O[5]=0, O[6]=0, O[7]=0, success=0 0->S0 1->S1
  S1       0100    O[0]=0, O[1]=0, O[2]=0, O[3]=0, O[4]=0, O[5]=0, O[6]=0, O[7]=0, success=0 0->S1 1->S2
  S2       0001    O[0]=0, O[1]=0, O[2]=0, O[3]=0, O[4]=0, O[5]=0, O[6]=0, O[7]=0, success=0 0->S2 1->S3
  S3       0101    O[0]=0, O[1]=0, O[2]=0, O[3]=0, O[4]=0, O[5]=0, O[6]=0, O[7]=0, success=0 0->S3 1->S4
  S4       0010    O[0]=0, O[1]=0, O[2]=0, O[3]=0, O[4]=0, O[5]=0, O[6]=0, O[7]=0, success=0 0->S4 1->S5
  S5       0110    O[0]=0, O[1]=0, O[2]=0, O[3]=0, O[4]=0, O[5]=0, O[6]=0, O[7]=0, success=0 0->S5 1->S6
  S6       0011    O[0]=0, O[1]=0, O[2]=0, O[3]=0, O[4]=0, O[5]=0, O[6]=0, O[7]=0, success=0 0->S6 1->S7
  S7       0111    O[0]=0, O[1]=0, O[2]=0, O[3]=0, O[4]=0, O[5]=0, O[6]=0, O[7]=0, success=0 0->S7 1->S8
  S8       1000    O[0]=0, O[1]=0, O[2]=0, O[3]=0, O[4]=0, O[5]=0, O[6]=0, O[7]=0, success=0 0->S8 1->S9
  S9       1100    O[0]=0, O[1]=0, O[2]=0, O[3]=0, O[4]=0, O[5]=0, O[6]=0, O[7]=0, success=0 0->S9 1->S10
  S10      1001    O[0]=0, O[1]=0, O[2]=0, O[3]=0, O[4]=0, O[5]=0, O[6]=0, O[7]=0, success=0 0->S10 1->S0

2-state machine in reg_enable (3 bits, 25% of the encoding space used)
  inputs: enable

  state    encoding outputs                  transitions
  S0 (reset) 111    O[0]=0, O[1]=0, O[2]=0, O[3]=0, O[4]=0, O[5]=0, O[6]=0, O[7]=0, success=0 0->S0 1->S1
  S1       000    O[0]=0, O[1]=0, O[2]=0, O[3]=0, O[4]=0, O[5]=0, O[6]=0, O[7]=0, success=0 0->S1 1->S1

```

Some interesting stuff here, although mostly likely noise, there is an ELEVEN state FSM that doesnt rely on the input `I`!! It counts through 0,1,2,3,4,5,6,7,8,9, and then goes to 12. But the bit ordering here is completely arbitrary. SO if we instead reorder the bits from [a,b,c,d] to [a,c,d,b] (see table, I might have errored in that ordering), we get a counter 0-10, i.e., a period of 11. That LINES UP with the IDEA I had above about looking for periodicity!

| State | Given encoding `[8, 1, 4, 2]` | Reordered as `[8,4,2,1]` | Count |
| ----- | ----------------------------- | ------------------------ | ----: |
| S0    | `0000`                        | `0000`                   |     0 |
| S1    | `0100`                        | `0001`                   |     1 |
| S2    | `0001`                        | `0010`                   |     2 |
| S3    | `0101`                        | `0011`                   |     3 |
| S4    | `0010`                        | `0100`                   |     4 |
| S5    | `0110`                        | `0101`                   |     5 |
| S6    | `0011`                        | `0110`                   |     6 |
| S7    | `0111`                        | `0111`                   |     7 |
| S8    | `1000`                        | `1000`                   |     8 |
| S9    | `1100`                        | `1001`                   |     9 |
| S10   | `1001`                        | `1010`                   |    10 |
| S0    | `0000`                        | `0000`                   |     0 |

This might be the first meaningful piece of insight into what the hell the real puzzle is doing. Found at 2:34am on a Tuesday morning. Bed time.
