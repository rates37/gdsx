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

## Aside: LEF reader

Since I wanted to turn this into a general use library eventually, I started collecting some ideas for features that would make this more generally useful. One idea was incorporating ability to read pins from LEF files. This warmup puzzle and the real one has text labels in the GDS, but in general the gds file isn't required to have that, and in those cases, the LEF file is teh source of truth for the pin names/locations for each cell.

This feature was mostly implemented by AI, since it isn't particularly important for the puzzle specifically, so may need "de-slopping" in the future.

The parser in `src/gdsx/lef.py` just parses what the `PinOracle` object needs, ignoring everything else. No need to make a full parser, way too complex and mostly irrelevant.

`_dbu(value: str, scale: float) -> int` does the conversion from microns to the GDS's internal integer grid units.

`tools/fetch_lef.py` pulls the LEF files for the cells a given .gds file uses. Currently just the sky130_fd_sc_hd library.

Then added the lef approach to the `PinOracle.pins` method to act as a fallback.

Based on how I feel later, may or may not remove this feature. Still undecided atm.

### Uncovered a Bug

Implementing this uncovered a bug in `Netlist.build`. It was originally sorting instances by `(cell_name, y, x)` to get a stable placement order, but the warm up design had two cells sitting at the exact same point in different orientations. Since position alone doesn't tie break, added the transform string into the sorting key to make the results deterministic:

```py
placements = sorted(
    design.instances(), key=lambda t: (t[0], t[1].disp.y, t[1].disp.x, str(t[1]))
)
```

## Attempting to find stuff out about the puzzle

I've spent a while implementing features, there are still more to implement but I want to start trying to use the tool since that will likely give me a better idea of what features to implement next to help the search.

```
$ uv run gdsx inspect samples/puzzle.gds
puzzle  dbu=0.001  layers=41
pin source: in-GDS labels
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━┓
┃ kind                       ┃ cells ┃ instances ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━┩
│ logic                      │ 66    │ 728       │
│ non-logic (tap/decap/fill) │ 3     │ 890       │
│ other (via/unknown)        │ 11    │ 8257      │
└────────────────────────────┴───────┴───────────┘

logic cells
    84 x sky130_fd_sc_hd__dfrtp_2
    49 x sky130_fd_sc_hd__nor2_2
    39 x sky130_fd_sc_hd__nand2_2
    31 x sky130_fd_sc_hd__o21a_2
    30 x sky130_fd_sc_hd__and2b_2
    29 x sky130_fd_sc_hd__xnor2_2
    26 x sky130_fd_sc_hd__a31o_2
    26 x sky130_fd_sc_hd__and3_2
    25 x sky130_fd_sc_hd__inv_2
    24 x sky130_fd_sc_hd__nand2b_2
    23 x sky130_fd_sc_hd__a22o_2
    21 x sky130_fd_sc_hd__xor2_2
    21 x sky130_fd_sc_hd__mux2_1
    20 x sky130_fd_sc_hd__a21oi_2
    19 x sky130_fd_sc_hd__a21o_2
    18 x sky130_fd_sc_hd__or3_2
    17 x sky130_fd_sc_hd__and2_2
    16 x sky130_fd_sc_hd__clkbuf_8
    15 x sky130_fd_sc_hd__clkbuf_4
    15 x sky130_fd_sc_hd__nand4_2
    14 x sky130_fd_sc_hd__and4bb_2
    13 x sky130_fd_sc_hd__or2_2
    12 x sky130_fd_sc_hd__o211a_2
    11 x sky130_fd_sc_hd__o31a_2
    10 x sky130_fd_sc_hd__or4_2
     9 x sky130_fd_sc_hd__or4b_2
     8 x sky130_fd_sc_hd__and4_2
     6 x sky130_fd_sc_hd__o21ai_2
     6 x sky130_fd_sc_hd__a221o_2
     6 x sky130_fd_sc_hd__conb_1
     5 x sky130_fd_sc_hd__a211o_2
     5 x sky130_fd_sc_hd__a32o_2
     5 x sky130_fd_sc_hd__nor3b_2
     4 x sky130_fd_sc_hd__a21boi_2
     4 x sky130_fd_sc_hd__dfxtp_2
     4 x sky130_fd_sc_hd__and4b_2
     4 x sky130_fd_sc_hd__nor3_2
     4 x sky130_fd_sc_hd__dfstp_2
     4 x sky130_fd_sc_hd__o22a_2
     4 x sky130_fd_sc_hd__and3b_2
     4 x sky130_fd_sc_hd__o32a_2
     3 x sky130_fd_sc_hd__a211oi_2
     3 x sky130_fd_sc_hd__o221a_2
     2 x sky130_fd_sc_hd__o21ba_2
     2 x sky130_fd_sc_hd__nand3_2
     2 x sky130_fd_sc_hd__nor4_2
     2 x sky130_fd_sc_hd__nor4b_2
     2 x sky130_fd_sc_hd__o311a_2
     2 x sky130_fd_sc_hd__o22ai_2
     2 x sky130_fd_sc_hd__a311o_2
     2 x sky130_fd_sc_hd__a21bo_2
     2 x sky130_fd_sc_hd__o31ai_2
     1 x sky130_fd_sc_hd__clkbuf_16
     1 x sky130_fd_sc_hd__a2111oi_2
     1 x sky130_fd_sc_hd__a22oi_2
     1 x sky130_fd_sc_hd__or3b_2
     1 x sky130_fd_sc_hd__a221oi_2
     1 x sky130_fd_sc_hd__a41oi_2
     1 x sky130_fd_sc_hd__or4bb_2
     1 x sky130_fd_sc_hd__buf_2
     1 x sky130_fd_sc_hd__nand3b_2
     1 x sky130_fd_sc_hd__o2bb2a_2
     1 x sky130_fd_sc_hd__a31oi_2
     1 x sky130_fd_sc_hd__o32ai_2
     1 x sky130_fd_sc_hd__o21bai_2
     1 x sky130_fd_sc_hd__o211ai_2

top-level labels
  I  (met3)
  O[0]  (met3)
  O[1]  (met3)
  O[2]  (met3)
  O[3]  (met3)
  O[4]  (met3)
  O[5]  (met3)
  O[6]  (met3)
  O[7]  (met3)
  VGND  (met4)
  VGND  (met5)
  VPWR  (met4)
  VPWR  (met5)
  clk  (met3)
  enable  (met3)
  rst_n  (met3)
  success  (met3)
```

So the ports are: serial `I`, 8-bit `O[7:0]`, `clk`, `enable`, `rst_n`, and `success`. This lines up with the sample vcd.

Extracting:

```
uv run gdsx extract samples/puzzle.gds -o out
```

```py
import json
d=json.load(open('out/puzzle.json'))
print('floating', len(d['floating']), 'conflicts', len(d['conflicts']), 'power', d['power_nets'])
from gdsx.pins import direction_of
cells={i['name']:i['cell'] for i in d['instances']}
multi=[];und=[]
for n,refs in d['nets'].items():
    if n in d['power_nets']: continue
    drv=[r for r in refs if direction_of(cells[r.split('/')[0]], r.split('/')[1])=='output']
    if len(drv)>1: multi.append(n)
    if not drv: und.append(n)
print('multi-driven:', multi)
print('undriven:', und)
print('nets', len(d['nets']))
```

Output:

```
floating 0 conflicts 0 power ['VGND', 'VPWR']
multi-driven: []
undriven: ['I', 'n4692', 'enable', 'clk', 'rst_n']
nets 741
```

```py
import json
d=json.load(open('out/puzzle.json'))
print('n4692:', d['nets']['n4692'])
```

Output:

```
n4692: ['a311o_2_2/A1', 'a31oi_2_1/A1']
```

So `n4692` is the only net that is undriven and not a top level port. Possibly an easter egg?

```py
from gdsx import config, loader, netlist, analyze
from gdsx.functions import is_sequential
nl = netlist.build(loader.load('samples/puzzle.gds', config.load()))
regs = analyze.find_registers(nl)
print('%d flops in %d registers'%(sum(1 for i in nl.instances if is_sequential(i.cell)), len(regs)))
for r in regs: print('  %-22s %s serial=%s' % (r.name, r.description, r.serial_input))
print()
dep = analyze.support(nl, 'success')
print('success depends on %d flops + ports %s' % (len([d for d in dep if not d in nl.ports]), sorted(d for d in dep if d in nl.ports)))
```

Output:

```
92 flops in 46 registers
  reg_dfrtp_2_11         1-bit shift register serial=None
  reg_dfrtp_2_12         1-bit shift register serial=None
  reg_dfrtp_2_13         2-bit shift register, bit order unknown serial=None
  reg_dfrtp_2_14         2-bit shift register, bit order unknown serial=None
  reg_dfrtp_2_19         1-bit shift register serial=None
  reg_dfrtp_2_21         3-bit feedback register, bit order unknown serial=None
  reg_dfrtp_2_22         2-bit shift register, bit order unknown serial=None
  reg_dfrtp_2_24         1-bit shift register serial=None
  reg_dfrtp_2_28         2-bit shift register, bit order unknown serial=None
  reg_dfrtp_2_3          5-bit feedback register serial=None
  reg_dfrtp_2_30         2-bit shift register, bit order unknown serial=None
  reg_dfrtp_2_32         2-bit shift register, bit order unknown serial=None
  reg_dfrtp_2_36         2-bit shift register, bit order unknown serial=None
  reg_dfrtp_2_44         3-bit feedback register serial=None
  reg_dfrtp_2_46         1-bit shift register serial=None
  reg_dfrtp_2_61         6-bit feedback register, bit order unknown serial=None
  reg_dfrtp_2_62         2-bit shift register, bit order unknown serial=None
  reg_dfrtp_2_64         1-bit shift register serial=None
  reg_dfrtp_2_65         1-bit shift register serial=None
  reg_dfrtp_2_66         2-bit shift register, bit order unknown serial=None
  reg_dfrtp_2_68         2-bit shift register, bit order unknown serial=None
  reg_dfrtp_2_7          2-bit shift register, bit order unknown serial=None
  reg_dfrtp_2_70         1-bit shift register serial=None
  reg_dfrtp_2_71         5-bit parallel register, bit order unknown serial=None
  reg_dfrtp_2_72         1-bit shift register serial=None
  reg_dfrtp_2_75         1-bit shift register serial=None
  reg_dfrtp_2_76         2-bit shift register, bit order unknown serial=None
  reg_dfrtp_2_80         1-bit shift register serial=None
  reg_dfrtp_2_81         1-bit shift register serial=None
  reg_dfrtp_2_82         3-bit shift register, bit order unknown serial=None
  reg_dfrtp_2_9          3-bit feedback register serial=None
  reg_dfstp_2_4          1-bit shift register serial=None
  reg_dfxtp_2_1          2-bit shift register, bit order unknown serial=None
  reg_dfxtp_2_3          2-bit shift register, bit order unknown serial=None
  reg_enable             3-bit feedback register, bit order unknown serial=None
  reg_enable             1-bit shift register serial=enable
  reg_enable             1-bit shift register serial=enable
  reg_enable             1-bit shift register serial=enable
  reg_enable             3-bit shift register serial=enable
  reg_enable             1-bit shift register serial=enable
  reg_enable             4-bit feedback register, bit order unknown serial=None
  reg_enable             1-bit shift register serial=enable
  reg_enable             1-bit shift register serial=enable
  reg_enable             3-bit feedback register, bit order unknown serial=None
  reg_enable             1-bit shift register serial=enable
  reg_enable             3-bit feedback register, bit order unknown serial=None

success depends on 1 flops + ports []
```

So success is registered flag. Tracing the flop level dependency graph from it:

```py
from gdsx import config, loader, netlist, analyse
from gdsx.functions import is_sequential, lookup, data_nets, output_net
nl = netlist.build(loader.load('samples/puzzle.gds', config.load()))
by = {i.name: i for i in nl.instances}
flops = [i.name for i in nl.instances if is_sequential(i.cell)]

# flop -> flops/ports its next state depends on
deps = {}
for f in flops:
    inst = by[f]; cell = lookup(inst.cell)
    d = set()
    for net in data_nets(cell, inst.connections):
        d |= analyse.support(nl, net)
    deps[f] = d

seed = [d for d in analyse.support(nl, 'success') if d in set(flops)]
print('success driven by flop:', seed)

# transitive fan-in over flops
cone, stack = set(seed), list(seed)
while stack:
    f = stack.pop()
    for d in deps[f]:
        if d in deps and d not in cone:
            cone.add(d); stack.append(d)
ports = set()
for f in cone: ports |= {d for d in deps[f] if d in nl.ports}
print('success cone: %d of %d flops; ports seen: %s' % (len(cone), len(flops), sorted(ports)))

# which flops are not in the cone
print('outside the cone: %d flops' % (len(flops)-len(cone)))
```

Output:

```
success driven by flop: ['dfrtp_2_83']
success cone: 79 of 92 flops; ports seen: ['I', 'enable']
outside the cone: 13 flops
```

Trying to see drivers:

```py
from gdsx import config, loader, netlist, analyse
from gdsx.functions import is_sequential, lookup, data_nets, clock_nets, async_nets
nl = netlist.build(loader.load('samples/puzzle.gds', config.load()))
by = {i.name: i for i in nl.instances}
flops = [i.name for i in nl.instances if is_sequential(i.cell)]
fs = set(flops)

deps = {}
for f in flops:
    inst = by[f]; cell = lookup(inst.cell)
    d = set()
    for net in data_nets(cell, inst.connections):
        d |= analyse.support(nl, net)
    deps[f] = d

# who does I feed directly
head = [f for f in flops if 'I' in deps[f]]
print('flops whose next state sees I:', head)
print()
# success flop chain
cur = 'dfrtp_2_83'
for depth in range(4):
    d = deps[cur]
    print(f'{cur}: flops={sorted(x for x in d if x in fs)} ports={sorted(x for x in d if x in nl.ports)}')
    nxt = sorted(x for x in d if x in fs and x != cur)
    if not nxt: break
    cur = nxt[0]
print()
# control signature grouping only
from collections import Counter
sig = Counter()
for f in flops:
    inst = by[f]; cell = lookup(inst.cell)
    sig[(inst.cell, tuple(sorted(clock_nets(cell, inst.connections))), tuple(sorted(async_nets(cell, inst.connections))))] += 1
for k, v in sig.most_common():
    print(f'{v:3d} flops: {k[0].split("__")[1]:12s} clk={k[1]} rst={k[2]}')
```

Output:

```
flops whose next state sees I: ['dfrtp_2_1', 'dfrtp_2_2', 'dfrtp_2_3', 'dfrtp_2_4', 'dfrtp_2_5', 'dfrtp_2_6', 'dfrtp_2_7', 'dfrtp_2_8', 'dfrtp_2_9', 'dfrtp_2_10', 'dfrtp_2_11', 'dfrtp_2_12', 'dfrtp_2_13', 'dfrtp_2_14', 'dfrtp_2_15', 'dfrtp_2_16', 'dfrtp_2_18', 'dfrtp_2_19', 'dfrtp_2_21', 'dfrtp_2_22', 'dfrtp_2_23', 'dfrtp_2_24', 'dfrtp_2_27', 'dfrtp_2_28', 'dfrtp_2_29', 'dfrtp_2_30', 'dfrtp_2_31', 'dfrtp_2_32', 'dfrtp_2_35', 'dfrtp_2_36', 'dfrtp_2_39', 'dfrtp_2_45', 'dfrtp_2_46', 'dfrtp_2_50', 'dfrtp_2_53', 'dfrtp_2_56', 'dfrtp_2_57', 'dfrtp_2_62', 'dfrtp_2_63', 'dfrtp_2_64', 'dfrtp_2_65', 'dfrtp_2_66', 'dfrtp_2_67', 'dfrtp_2_68', 'dfrtp_2_69', 'dfrtp_2_70', 'dfrtp_2_71', 'dfrtp_2_72', 'dfrtp_2_73', 'dfrtp_2_74', 'dfrtp_2_75', 'dfrtp_2_76', 'dfrtp_2_78', 'dfrtp_2_79', 'dfrtp_2_80', 'dfrtp_2_81', 'dfrtp_2_84', 'dfstp_2_4']

dfrtp_2_83: flops=['dfrtp_2_1', 'dfrtp_2_10', 'dfrtp_2_11', 'dfrtp_2_12', 'dfrtp_2_13', 'dfrtp_2_14', 'dfrtp_2_15', 'dfrtp_2_16', 'dfrtp_2_18', 'dfrtp_2_19', 'dfrtp_2_2', 'dfrtp_2_22', 'dfrtp_2_23', 'dfrtp_2_24', 'dfrtp_2_28', 'dfrtp_2_29', 'dfrtp_2_3', 'dfrtp_2_30', 'dfrtp_2_31', 'dfrtp_2_32', 'dfrtp_2_35', 'dfrtp_2_36', 'dfrtp_2_39', 'dfrtp_2_4', 'dfrtp_2_45', 'dfrtp_2_46', 'dfrtp_2_5', 'dfrtp_2_50', 'dfrtp_2_56', 'dfrtp_2_57', 'dfrtp_2_6', 'dfrtp_2_61', 'dfrtp_2_62', 'dfrtp_2_63', 'dfrtp_2_64', 'dfrtp_2_65', 'dfrtp_2_66', 'dfrtp_2_67', 'dfrtp_2_68', 'dfrtp_2_69', 'dfrtp_2_7', 'dfrtp_2_70', 'dfrtp_2_71', 'dfrtp_2_72', 'dfrtp_2_73', 'dfrtp_2_74', 'dfrtp_2_75', 'dfrtp_2_76', 'dfrtp_2_78', 'dfrtp_2_79', 'dfrtp_2_8', 'dfrtp_2_80', 'dfrtp_2_81', 'dfrtp_2_82', 'dfrtp_2_83', 'dfrtp_2_84', 'dfrtp_2_9'] ports=[]
dfrtp_2_1: flops=['dfrtp_2_1', 'dfrtp_2_2', 'dfrtp_2_3', 'dfrtp_2_6', 'dfrtp_2_61', 'dfrtp_2_9'] ports=['I', 'enable']
dfrtp_2_2: flops=['dfrtp_2_2', 'dfrtp_2_61', 'dfrtp_2_9'] ports=['I', 'enable']
dfrtp_2_61: flops=['dfrtp_2_17', 'dfrtp_2_20', 'dfrtp_2_25', 'dfrtp_2_26', 'dfrtp_2_40', 'dfrtp_2_41', 'dfrtp_2_47', 'dfrtp_2_51', 'dfrtp_2_61'] ports=['enable']

  6 flops: dfrtp_2      clk=('n3801',) rst=('rst_n',)
  6 flops: dfrtp_2      clk=('n3978',) rst=('rst_n',)
  6 flops: dfrtp_2      clk=('n4836',) rst=('rst_n',)
  6 flops: dfrtp_2      clk=('n3503',) rst=('rst_n',)
  6 flops: dfrtp_2      clk=('n3286',) rst=('rst_n',)
  6 flops: dfrtp_2      clk=('n2227',) rst=('rst_n',)
  6 flops: dfrtp_2      clk=('n3319',) rst=('rst_n',)
  6 flops: dfrtp_2      clk=('n1524',) rst=('rst_n',)
  6 flops: dfrtp_2      clk=('n1015',) rst=('rst_n',)
  6 flops: dfrtp_2      clk=('n771',) rst=('rst_n',)
  5 flops: dfrtp_2      clk=('n5358',) rst=('rst_n',)
  5 flops: dfrtp_2      clk=('n2993',) rst=('rst_n',)
  5 flops: dfrtp_2      clk=('n622',) rst=('rst_n',)
  3 flops: dfrtp_2      clk=('n1829',) rst=('rst_n',)
  3 flops: dfrtp_2      clk=('n463',) rst=('rst_n',)
  3 flops: dfrtp_2      clk=('n230',) rst=('rst_n',)
  3 flops: dfstp_2      clk=('n1829',) rst=('rst_n',)
  2 flops: dfxtp_2      clk=('n463',) rst=()
  2 flops: dfxtp_2      clk=('n230',) rst=()
  1 flops: dfstp_2      clk=('n463',) rst=('rst_n',)
```

So the clock tree is buffered into a bunch of different clock nets. TThis means my control-signature grouping split registers by _clock buffer_ rather than by register. That's a real bug, and fixing it now.

Attempting to BFS the flop state looking for success:

```py
from collections import deque
from gdsx import config, loader, netlist
from gdsx.sim import Simulator

nl = netlist.build(loader.load('samples/puzzle.gds', config.load()))
sim = Simulator(nl)
order = [i.name for i, _ in sim.flops]

def snapshot(): return tuple(sim.state[f] for f in order)
def restore(s): sim.state = dict(zip(order, s))

sim.reset()
sim.step({'clk':0,'rst_n':0,'enable':0,'I':0})
start = snapshot()

seen = {start: None}          # state -> (parent, bit)
queue = deque([start])
found = None
steps = 0
while queue and steps < 400000:
    state = queue.popleft()
    for bit in (0, 1):
        restore(state)
        v = sim.step({'clk':0,'rst_n':1,'enable':1,'I':bit})
        steps += 1
        nxt = snapshot()
        if v['success']:
            found = (state, bit, nxt); queue.clear(); break
        if nxt not in seen:
            seen[nxt] = (state, bit)
            queue.append(nxt)
    if found: break

print('states explored:', len(seen), 'transitions:', steps)
if found:
    state, bit, _ = found
    path = [bit]
    while seen[state] is not None:
        state, b = seen[state]
        path.append(b)
    path.reverse()
    print('SUCCESS after %d bits: %s' % (len(path), ''.join(map(str, path))))
else:
    print('no success found')
```

It takes way too long, can't do it with this approach.

Also found another issue, when the verilog is generated, it uses `O[0]` as the name of the first output rather than `O[7:0]` as the name of the output bus. THis means the generated verilog code wasn't legal verilog, won't run in a simulator. For now, lazily escaped the identifier. Should probably fix this later too. Done in commit: b524f7a.

## Attempt 2:

Slept and having another attempt at some stuff:

### Check what undriven nets are connected to:

```py
from gdsx import config, loader, netlist, analyse

nl = netlist.build(loader.load('samples/puzzle.gds', config.load()))
print('floating pins:', nl.floating)
print('conflicts:', nl.conflicts)

drivers = analyse._drivers(nl)
undriven = [n for n in nl.nets if n not in drivers and n not in nl.power_nets and n not in nl.ports]
print('undriven nets (excluding ports/power):', undriven)
for net in undriven:
    print(f'  {net}: referenced by {nl.nets[net]}')
    cone = analyse.cone_instances(nl, {'success'}, set())
    print(f'    in success cone: {net in cone}')
    for port in ['success'] + [f'O[{i}]' for i in range(8)]:
        c = analyse.cone_instances(nl, {port}, set())
        readers = [ref.split('/')[0] for ref in nl.nets[net]]
        hit = [r for r in readers if r in c]
        if hit:
            print(f'    reaches {port} via {hit}')
```

Output:

```
floating pins: []
conflicts: []
undriven nets (excluding ports/power): ['n4692']
  n4692: referenced by ['a311o_2_2/A1', 'a31oi_2_1/A1']
    in success cone: False
    reaches O[1] via ['a311o_2_2', 'a31oi_2_1']
    reaches O[4] via ['a31oi_2_1']
```

There is one undriven net, but it's not connected to the success cone. So it might affect the output bits, but not whether success is reached. Possible easter egg? Or maybe just a red herring.

### Checking FF depedency from `I`:

```py
from collections import deque
from gdsx import config, loader, netlist, analyse
from gdsx.functions import is_sequential, lookup, data_nets, output_net

nl = netlist.build(loader.load('samples/puzzle.gds', config.load()))
by = {i.name: i for i in nl.instances}
flops = [i.name for i in nl.instances if is_sequential(i.cell)]
fs = set(flops)
deps = {}
for f in flops:
    inst = by[f]
    cell = lookup(inst.cell)
    d = set()
    for net in data_nets(cell, inst.connections):
        d |= analyse.support(nl, net)
    deps[f] = d

fwd = {f: set() for f in flops}
for f in flops:
    for d in deps[f]:
        if d in fs:
            fwd[d].add(f)

seeds = [f for f in flops if 'I' in deps[f]]
depth = {f: 0 for f in seeds}
q = deque(seeds)
while q:
    f = q.popleft()
    for n in fwd[f]:
        if n not in depth:
            depth[n] = depth[f] + 1
            q.append(n)

print('flops directly fed by I:', len(seeds))
print('max flop-graph depth from I:', max(depth.values()) if depth else None)
print('total flops:', len(flops))

success_flop = None
for f in flops:
    net = output_net(lookup(by[f].cell), by[f].connections)
    if net == 'success':
        success_flop = f
        break
print('flop driving success directly:', success_flop)
if success_flop:
    print('depth of the success flop:', depth.get(success_flop))
    print('flops in cone of success data-input:', len(deps[success_flop] & fs))

selfloop = [f for f in flops if f in deps[f]]
print('flops that feed themselves (counters/holds):', len(selfloop))

pure = [f for f in flops if not (deps[f] & fs)]
print('flops fed only by ports:', pure)
```

Output:

```
flops directly fed by I: 58
max flop-graph depth from I: 11
total flops: 92
flop driving success directly: dfrtp_2_83
depth of the success flop: 1
flops in cone of success data-input: 57
flops that feed themselves (counters/holds): 92
flops fed only by ports: []
```

58 registers directly (or through combinational logic) read `I`. Max depth of 11, which is the second time the number 11 has come up in this puzzle. Probably a coincidence but maybe easter egg.

At this point it still seems a little too complex of a task to manually piece together, so I'll keep adding more features to the tool to help with that.

I found out about yosys `sat -seq N` which can do SAT solving on sequential circuits, but I don't like the idea of using that, as it would just be a black box and not give much insight into the puzzle. Good to know as a last resort if I give up, but I really like the idea of doing this in a more understandable way.

## Cheaper Equivalent Checking

I already made the `gdsx verify` command, which used temporal induction, flattens two designs, build a miter, and asks the Yosys SAT solver to induct over the entire state space. But this is expensive, especially for the puzzle (when verifying the primitive vs lifted), which just timed out in my testing.

Instead, since most internal signals are the same, we can check for structural equivalence using `equiv_name`/`equiv_induct` instead of `miter`/`sat -tempinduct`:

```py
STRUCTURAL_SCRIPT = """\
read_verilog {primitives} {reference}
prep -top {top} -flatten
async2sync
opt_clean
design -stash gold

read_verilog {primitives} {extracted}
prep -top {top} -flatten
async2sync
opt_clean
design -stash gate

design -copy-from gold -as gold {top}
design -copy-from gate -as gate {top}
equiv_make gold gate equiv
hierarchy -top equiv
equiv_simple -seq {seq}
equiv_induct -seq {seq}
equiv_status -assert
"""
```

Now added a `--structural` flag to `gdsx verify` to use this structural equivalence check instead of the miter based SAT check.

## Normalisation

Since plan is to eventually lift the Verilog to more readable form, need to reverse transformations that was applied during synthesis / P&R. E.g., a clock signal doesn't directly connect to all FFs in the design, but rather goes through a clock tree of `clkbuf` cells first. This makes the primitive verilog very hard to read, as the clock tree is just noise. Added `src/gdsx/normalise.py`.

There are three main transforms:

1. constant propagation: any constants are propagated through the design. Any gate whose output is fixed regardless of its inputs can be replaced with a constant.

2. Identity collapse: any buffers essentially do nothing for the behaviour of the circuit (in simulation), so they can be combined. E.g., all the clock buffers, and any other buffers that pass a signal through to meet timing requirements.

3. Inverter pairs in series can collapse into a short.

### Union Find again

To merge/combine nets, it uses a Union find, just like before. Implemented in the `_Aliases` class. However, here the tie breaking over which name is kept is important. Port names always win, as a port name that gets merged into an internal net would change the module interface. Otherwise, the driver side of the merge wins, so a chain of buffers collapses towards the source, rather than towards the leaf.

### Exhaustive Evaluation

The `_simplify` function asks "given the knowns about the inputs of a cell, is the output still dependent on the inputs?":

```py
def _simplify(cell, inst: Instance, value_of) -> tuple[dict[str, int], dict[str, str]]:
    if cell.is_sequential or not cell.has_behaviour:
        return {}, {}
    known: dict[str, int] = {}
    unknown: list[str] = []
    # ommitted collection of known/unknown inputs from this snippet for brevity
    rows = []
    for bits in product((0, 1), repeat=len(unknown)):
        values = dict(known, **dict(zip(unknown, bits)))
        rows.append((values, cell.evaluate(values)))

    constants: dict[str, int] = {}
    copies: dict[str, str] = {}
    for pin in rows[0][1]:
        seen = {out[pin] for _, out in rows}
        if len(seen) == 1:
            constants[pin] = seen.pop()
            continue
        for candidate in unknown:
            if all(out[pin] == inputs[candidate] for inputs, out in rows):
                copies[pin] = candidate
                break
    return constants, copies
```

This is just brute-force enumeration over the still unknown inputs. I wrote about a similar implementation I did for a side project here: https://github.com/rates37/openxc2064/blob/main/docs/optimiser.md

This process is then repeated in a loop until no more simplifications can be made.

Exmaple:

```
uv run gdsx normalise samples/puzzle.gds -o out/mystery_normalised
728 -> 688 instances, 741 -> 696 nets
45 cells removed, 45 nets merged
    33 buffers collapsed
     1 inverter pairs collapsed
    11 cells folded to a constant
     0 cells degenerated into a wire
     0 cells driving nothing
    13 nets known constant

buffers
  buf_2_1              sky130_fd_sc_hd__buf_2 n3049 := n3048
  clkbuf_16_1          sky130_fd_sc_hd__clkbuf_16 n1034 := clk
  clkbuf_4_1           sky130_fd_sc_hd__clkbuf_4 n1 := n3978
  clkbuf_4_2           sky130_fd_sc_hd__clkbuf_4 n3981 := n3801
  clkbuf_4_3           sky130_fd_sc_hd__clkbuf_4 n5399 := n4836
  clkbuf_4_4           sky130_fd_sc_hd__clkbuf_4 n5352 := n5358
  clkbuf_4_5           sky130_fd_sc_hd__clkbuf_4 n3378 := n3503
  clkbuf_4_6           sky130_fd_sc_hd__clkbuf_4 n3443 := n3286
  clkbuf_4_7           sky130_fd_sc_hd__clkbuf_4 n3351 := n2993
  clkbuf_4_8           sky130_fd_sc_hd__clkbuf_4 n3335 := n3319
  clkbuf_4_9           sky130_fd_sc_hd__clkbuf_4 n2219 := n2227
  clkbuf_4_10          sky130_fd_sc_hd__clkbuf_4 n2139 := n1829
  clkbuf_4_11          sky130_fd_sc_hd__clkbuf_4 n1435 := n1524
  clkbuf_4_12          sky130_fd_sc_hd__clkbuf_4 n1420 := n1015
  clkbuf_4_13          sky130_fd_sc_hd__clkbuf_4 n287 := n230
  clkbuf_4_14          sky130_fd_sc_hd__clkbuf_4 n1033 := n771
  clkbuf_4_15          sky130_fd_sc_hd__clkbuf_4 n1164 := n622
  clkbuf_8_1           sky130_fd_sc_hd__clkbuf_8 n3801 := clk
  clkbuf_8_2           sky130_fd_sc_hd__clkbuf_8 n3978 := clk
  clkbuf_8_3           sky130_fd_sc_hd__clkbuf_8 n4836 := clk
  clkbuf_8_4           sky130_fd_sc_hd__clkbuf_8 n5358 := clk
  clkbuf_8_5           sky130_fd_sc_hd__clkbuf_8 n3503 := clk
  clkbuf_8_6           sky130_fd_sc_hd__clkbuf_8 n3286 := clk
  clkbuf_8_7           sky130_fd_sc_hd__clkbuf_8 n3319 := clk
  clkbuf_8_8           sky130_fd_sc_hd__clkbuf_8 n2993 := clk
  clkbuf_8_9           sky130_fd_sc_hd__clkbuf_8 n2227 := clk
  clkbuf_8_10          sky130_fd_sc_hd__clkbuf_8 n1829 := clk
  clkbuf_8_11          sky130_fd_sc_hd__clkbuf_8 n1524 := clk
  clkbuf_8_12          sky130_fd_sc_hd__clkbuf_8 n1015 := clk
  clkbuf_8_13          sky130_fd_sc_hd__clkbuf_8 n463 := clk
  clkbuf_8_14          sky130_fd_sc_hd__clkbuf_8 n230 := clk
  clkbuf_8_15          sky130_fd_sc_hd__clkbuf_8 n622 := clk
  clkbuf_8_16          sky130_fd_sc_hd__clkbuf_8 n771 := clk

inverter pairs
  inv_2_24             sky130_fd_sc_hd__inv_2 n127 := n2951

constant folds
  conb_1_2             sky130_fd_sc_hd__conb_1 n2949 = 1
  conb_1_2             sky130_fd_sc_hd__conb_1 n3435 = 0
  conb_1_3             sky130_fd_sc_hd__conb_1 n4677 = 1
  conb_1_3             sky130_fd_sc_hd__conb_1 n6854 = 0
  conb_1_4             sky130_fd_sc_hd__conb_1 n4512 = 1
  conb_1_4             sky130_fd_sc_hd__conb_1 n533 = 0
  conb_1_5             sky130_fd_sc_hd__conb_1 n4433 = 1
  conb_1_5             sky130_fd_sc_hd__conb_1 n6992 = 0
  conb_1_6             sky130_fd_sc_hd__conb_1 n2439 = 1
  conb_1_6             sky130_fd_sc_hd__conb_1 n469 = 0
  a22o_2_17            sky130_fd_sc_hd__a22o_2 n549 = 0
  wrote out/mystery_normalised/puzzle.json
  wrote out/mystery_normalised/puzzle.v
  wrote out/mystery_normalised/puzzle.generic.v
  wrote out/mystery_normalised/puzzle.generic.json
  wrote out/mystery_normalised/puzzle.dot
```

## xref

Trying to dedup/generalise some logic. Prior analysis often needs to ask something like what drives a given net, what's between/cutting these two nodes, etc. `src/gdsx/xref.py` generalises this

```py
@dataclass(frozen=True)
class Ref:
    """One pin on one instance, and which way it faces"""
    instance: str
    pin: str
    cell: str
    direction: str  # input | output | power

@dataclass
class Xref:
    net: str
    drivers: list[Ref] = field(default_factory=list)
    readers: list[Ref] = field(default_factory=list)
    port: str | None = None  # "input"/"output" if the net reaches the boundary
```

`refs(nl, net)` returns an `Xref` object on what drives and reads a net.

E.g.,:

```py
from gdsx import config, loader, netlist, xref
nl = netlist.build(loader.load('samples/sample.gds', config.load()))
print(xref.report(xref.refs(nl, 'n629')))
```

Output:

```
net n629
  <- and2_2_3.X (sky130_fd_sc_hd__and2_2)
  -> a21bo_2_1.A2 (sky130_fd_sc_hd__a21bo_2)
  -> a31o_2_1.A2 (sky130_fd_sc_hd__a31o_2)
  -> xor2_2_5.B (sky130_fd_sc_hd__xor2_2)
```

### Fan-in/out:

`fanin`/`fanout` walk the graph outward from a net and stop at flops (by default).

```py
def fanin(nl: Netlist, net: str, depth: int = 3, through_flops: bool = False):
    """Nets upstream of `net`, level by level

    Stops at flops by default: past a flop you are in the previous clock cycle,
    which is a different question from "what makes this value".
    """
    # ..

def fanout(nl: Netlist, net: str, depth: int = 3, through_flops: bool = False):
    """Nets downstream of `net`, level by level"""
    # ...
```

### `between` slice

Another function here is `between(sources, sinks)`:

```py
def between(nl: Netlist, sources: set[str], sinks: set[str], through_flops: bool = True) -> set[str]:
    """Instances on a path from any source net to any sink net

    Forwards from the sources and backwards from the sinks, intersected
    """
```

## Regions

The xref `between` slices the computational graph. But given the hint that the puzzle layout hints at its function, slicing by spatial region is also important.

```py
@dataclass
class Region:
    box: Box
    inside: list[str]
    netlist: Netlist
    total_cells: int
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    internal: list[str] = field(default_factory=list)
```

We can use the "cut ratio" (fraction of the region's nets that cross its boundary) as a heuristic for finding a good region to cut. Cutting a lot of nets suggests you cut an important submodule in half, whereas only having a few nets that enter/leave the region gives you a good suggestion that what you cut is a self-contained piece of functionality.

## Guards

When synthesised, something like `if (enable) q <= d` becomes a self-feeding 2:1 mux in front of the flip flop, and after cells mapping, mux gets implemented based on what the mapper chose. This means every FFs fan-in cone contains its own output, because the hold path (when enable=0) reads the FFs output. Then `find_registers` builds a bunch of self-loops and it's harder to see the underlying structure, all because of the presence of an `enable` signal.

Running `normalise` with `seed={net:value}` then ends up checking if the FFs data input ends up aliased to the same net as its own output:

```py
def find(nl, *, min_fanout=MIN_FANOUT, nets=None):
    """For every FF, the conditions under which its data input becomes its own output"""
    state = _state(nl)
    tested = nets if nets is not None else candidates(nl, min_fanout)
    # ...
    for net in tested:
        for value in (0, 1):
            alias = normalise(nl, seed={net: value}, clean=False).merges
            for flop, (d_nets, q) in state.items():
                target = settle(alias, q)
                if all(settle(alias, d) == target for d in d_nets):
                    result.guards.append(Guard(flop, net, value))
    return result
```

A FF with `D = Q` (a no-op) woudl trivially match the assumptions, since it's data input already equals its output regardless of any cofactor, resulting a reporting a meaningless "guard" for every candidate tested. SO add an extra check to catch this:

```py
baseline = normalise(nl, clean=False).merges
...
state = {
    flop: (d, q)
    for flop, (d, q) in state.items()
    if not all(settle(baseline, n) == settle(baseline, q) for n in d)
}
```

Running this on the sample, it correctly identifies all the FFs in the sample puzzle have a shared enable signal:

```
$ uv run gdsx guards samples/sample.gds
21 candidate control nets tested against 16 flops
16 flops have a recovered freeze condition, 0 do not

GROUPS  (flops that are enabled together)
   16 flops frozen when en=0
        dfrtp_2_1, dfrtp_2_10, dfrtp_2_11, dfrtp_2_12, dfrtp_2_13, dfrtp_2_14
        dfrtp_2_15, dfrtp_2_16, dfrtp_2_2, dfrtp_2_3, dfrtp_2_4, dfrtp_2_5
        dfrtp_2_6, dfrtp_2_7, dfrtp_2_8, dfrtp_2_9
```

## Floorplan

Added (AI sloppified) functionality to turn a grouping into an SVG plotted at the real coordinates of the cells. Probably will remove this, it's not super important, and not well implemented.

## Read Netlist json back in

Added a `Netlist.from_dict()` method to read back in a netlist that was written out to JSON. Useful for analysis and extraction. E.g., `gdsx region ... -o out` writes the carved netlist from a region to disk in json format, so now can be read back in.

```
$ uv run gdsx region samples/sample.gds --band 0 -o out
region (20.08, 19.76) .. (48.38, 86.80)
  35 of 79 cells (44%)
  5 inputs, 16 outputs, 19 internal nets
  cut ratio 0.53 -- a block with a lot of context
  inputs:  A, B, clk, en, rst_n
  outputs: n104, n168, n176, n191, n208, n209, n303, n345, n410, n426, n427, n445
  wrote out/adder_demo_band0.json
  wrote out/adder_demo_band0.v
  wrote out/adder_demo_band0.generic.v
  wrote out/adder_demo_band0.generic.json
  wrote out/adder_demo_band0.dot
```

Now that JSON can be passed to other commands in place of the gds file path:

```
$ uv run gdsx analyse out/adder_demo_band0.json
registers
  reg_A: 8-bit shift register <- A
    dfrtp_2_12 -> dfrtp_2_10 -> dfrtp_2_15 -> dfrtp_2_11 -> dfrtp_2_14 -> dfrtp_2_13 -> dfrtp_2_16 -> dfrtp_2_9
  reg_B: 8-bit shift register <- B
    dfrtp_2_2 -> dfrtp_2_5 -> dfrtp_2_4 -> dfrtp_2_6 -> dfrtp_2_1 -> dfrtp_2_8 -> dfrtp_2_3 -> dfrtp_2_7

blocks (functional match, with the gates backing each call)
  reg_A: 8-bit shift register  (16 cells)
  reg_B: 8-bit shift register  (16 cells)
  clock_tree: buffers driving the flop clocks  (3 cells)

operators (proven over the full input space)
  n104 asserted for 32768 of 65536 states. no known operator matches
  ...
```

## Synth RTL -> Netlist moved out of test suite

In `tests/rtl_fixtures` there was already a flow of write verilog -> run through yosys -> rename the gates to sky130 equivalents and get something relatively indistinguishable from the extracted netlist.

Does renaming instead of actual synthesis directly to sky130 so that there isn't a dependence on needing the PDK. Only needs the small json it fetches once.

## Interface inference based on behaviour

Earlier phases assumed meaning of top level ports based on the name (clk, rst_n, en, etc.). Instead, I thought it would be cool to be able to infer the meaning of input ports based on their behaivour. THere's aleready all this functionality in the library for analysis, but it's all separate in different commands. They aren't useless but it's a bit overwhelming to have to go through so much documentation to figure out how to use them. Not that this will replace them but for a quick surface level look, this can provide some initial direction.

Note: I think for the purpose of the puzzle this is useless bc the puzzle does have meaninful port names (clk, I, O, en, success, etc.).

### Input classification

The five key input behaviours to check for are:

- clock/reset: easy to tell as it will reach the clock or async reset pin of FFs
- gate: holding at once value freezes the design (e.g., enable pin)
- data(generic): changing this changes the outputs/state, most pins fall here, and doesn't provide much meaningful info
- combinational: reaches the outputs directly, never touches a FF
- unused: find pins that are literally meaningless

### Output classification

Way harder to classify outputs, especially for the puzzles because something like success would only go high when the correct input sequence is applied. I.e., basically never if you don't know the correct sequence. So for now it's between `static`, `registered`, and `combinational`. Since the puzzle sequence isn't known, it currently labels the success output as `static` (and the `O` outputs).

Now using it on the puzzle, it tells me basically what we already knew:

```
$ uv run gdsx ports samples/puzzle.gds
inputs

  I              input  data (reaches 58 flops)
  clk            input  clock (drives the flop clock pins)
  enable         input  gate (held at 0 nothing changes)
  rst_n          input  reset (drives the flop set/clear pins, asserted 0)

outputs

  O[0]           static
  O[1]           static
  O[2]           static
  O[3]           static
  O[4]           static
  O[5]           static
  O[6]           static
  O[7]           static
  success        static
```

## More misc Notes:

Found this from a 2017 CTF: https://blog.dragonsector.pl/2017/10/?m=1

Methodology there sounds similar to what I was saying about using a sequential SAT solver to find the input sequence that leads to success. But like before I'd rather not do that (at least at first) and gradually recover the structure of the design.

## Circuit Idioms

`analyse.OPERATORS` finds multi bit operators (like adders, subtractors, logical operators) by hypothesis, confirming candidates with random sampling + exhaustive search / SAT solve. `idiom.py` uses a different approach, instead looking for the pattern of gates that implement a given operator.

Every net is the output of some small subgraph/subcircuit. Enumerating for each net, every small set of nets whose values determine it gives a small subsection of the design worth checking without needing to guess where a meaningful boundary might be.

To get a cut, we take one cut from each of the driving cell's inputs and union them, keeping unions that stay within a given size limit. This recurses backwards through the graph, again with a depth limit to avoid compute requirements exploding.

### Canonical Form

Two of the same circuit, eg an adder, built by different synthesisers, or run through different flows like produce different physical circuits. Some inputs may have different orders, some inverted, etc. Instead of matching, we can normalise to a canonical form to compare against. Here, using Negation/Permutation of Inputs, Negation of the output (NPN) form:

```py
@lru_cache(maxsize=1 << 16)
def npn(table: int, width: int) -> int:
    """The canonical form of a function under input/output negation and permutation"""
```

At width 4 that's $2^4 \times 4! \times 2 = 768$ transformations per function, and `lru_cache`ed avoids recomputing as well.

### The Library:

Created a small hand written table of named functions, canonicalised once at import time, keyed by `(width, canonical form)`:

```py
@lru_cache(maxsize=1)
def library():
    """(inputs, canonical form) -> name."""
    found = {}
    for name, width, function in DEFINITIONS:
        key = (width, npn(table_of(width, function), width))
        if key in found and name not in found[key].split(" / "):
            found[key] = f"{found[key]} / {name}"
        else:
            found.setdefault(key, name)
    return found
```

To match a candidate cut against the library, we evaluate its truth table, canonicalise it, and look it up in the library.

### Matching

`match()` walks every net's cuts from widest to narrowest and keeps the widest one that is in the library.

### Composition

`carry_chains(matches)` follows the structural relationship between matched carry bits, in order to identify multi-bit adders. Todo: expand this to other operators like sub. Investigate if other things like barrel shifters, etc.

### Running on the warmup:

```py
from gdsx import config, loader, netlist, idiom
nl = netlist.build(loader.load('samples/sample.gds', config.load()))
matches = idiom.match(nl)
chains = idiom.carry_chains(matches)
print(idiom.report(nl, matches, chains))
```

Output:

```
35 nets match a known function (139 cuts examined, 12 idioms in the library)

    16 x mux                    n108, n135, n179, n205, n207, n211 ...
     7 x adder sum / parity     n14, n16, n3, n576, n656, n668 ...
     5 x compare bit (a>b)      n581, n678, n705, n741, n800
     4 x adder carry (majority) n18, n587, n687, n842
     3 x and/or of 4            S, n19, n658

1 carry chains:
  2-bit adder: carries n687 -> n18

  Matched by canonical function, so a resynthesised adder still matches.
  Nothing here is a claim about what the operands mean.
```

The 16 shift register hold muxes are found straight away. Some of the other stuff is less obvious or just completely misleading. A bit of a shame. But still, maybe this is useful once the circuit is a little more broken up into submodules?

## Recovering register bit order by probing

`find_registers` (from a long way up) grouped registers and then attempted to order them. And parallel-load register's bits never talk to one another, which was a noted limitation, as it meant there is nothing in the dependency graph to sort them.

The design's output ports carry indexing, like `O[7]`, `O[6]`, ... And in that case, it's trivial to get the bit ordering!

### Probing:

`probe_positions` answers which bit a FF corresponds to, by setting one FF's state to 1 with everything else at 0, simulating, and seeing which single bit of the word changed.

```py
def probe_positions(nl, register, inputs=None):
    """word -> {flop: bit index}, for the words each flop lands on cleanly

    Per word, because one FF may reaches several. E.g., bit 2 of a register
    is bit 2 of the register's own output and three bits of the adder it
    feeds. The register word is the one where it moves a single bit.
    """
```

Again unlikely to be super useful here, but maybe in decoding the `O[7:0]` bits. But then again, the puzzle hinted that the `O` bits are driven by the decoding logic, which isn't necessary for deriving the input sequence. That being said, can't hurt.

```py
nl = rtl_fixtures.from_verilog(rtl_fixtures.PARALLEL_LOAD, "parallel_load", d)
registers = analyse.find_registers(nl)
```

Output before this change:

```
8-bit parallel register, bit order unknown   (order_evidence = topology)
```

Output after this change:

```
after:  q  8-bit parallel register   (order_evidence = probe)
        ['dfrtp_1', 'dfrtp_2', 'dfrtp_3', 'dfrtp_4', 'dfrtp_5', 'dfrtp_6', 'dfrtp_7', 'dfrtp_8']
```

Still unsure if keeping this feature. I'm not convinced it's definitely useful for the puzzle.

## Register Purpose ID:

Now trying to answer the question what each register is acutalyly doing (counter, accumulator, LFSD, etc.). This can be read from netlist topology

Five categories:

- counter: feedback through an adder who's only operand is the register itself
- accumulator: feedback through an adder that also reads something else
- LFSR: feedback through XOR gates acting on its own bits
- input register: no feedback, just fed from input ports

This is useful because a counter and a register would appear similar if only considering the register nets, but by considering the surrounding gates, we can get suggestions for what the register is actually used for in the physcial design. Like the difference between `Q <= Q + 1` and `Q <= Q + d` is whether the adder's second operand is a constant or comes from another net within the circuit.

`sequential.graph(nl, registers)` builds a map of register -> the registers its next state depends on. Needs the whole cone, not just what's directly on the D pin, so that we can fully understand what drives that register.

### Exmaple on Warmup:

```py
from gdsx import config, loader, netlist, analyse, sequential
nl = netlist.build(loader.load('samples/sample.gds', config.load()))
registers = analyse.resolve_bit_order(nl, analyse.find_registers(nl))
roles = sequential.classify(nl, registers)
print(sequential.report(nl, roles, sequential.pipelines(roles)))
```

Output:

```
2 registers over 16 flops

    2 x shift register
        reg_A   [bits feed the next]
        reg_B   [bits feed the next]
```

Unfortunately something like a multi-bit wide pipeline like `a <= d; b <= a; c <= b;` gets shown as eight separate 3-bit shift registers: (needs looking at again in the future)

```
# PIPELINE, 8 bits wide, 3 stages
8 registers over 24 flops

    8 x shift register
        reg_d_0   [bits feed the next]
        reg_d_1   [bits feed the next]
        ...
```

On the real puzzle:

```
3 registers over 92 flops

    1 x accumulator
        reg_dfrtp_2_41  <- reg_dfstp_2_1, reg_dfxtp_2_1   [feedback through a carry bit]
    1 x LFSR
        reg_dfstp_2_1  <- reg_dfrtp_2_41, reg_dfxtp_2_1   [shift with 10 parity taps]
    1 x state register
        reg_dfxtp_2_1  <- reg_dfrtp_2_41   [depends on itself]
```

Not much informative there sadly.

## Yet Another Attempt At Decoding the Puzzle

Again, I think I've added enough capabilities to the tool to start trying to decode the puzzle. My hope is that once one or two pieces are figured out, the rest would follow easier. Also, I'll move to just writing scripts since that's faster, rather than integrating with the tool straight away. Once (if) I figure out the puzzle, will be able to see what features are actually useful to include in the tool and what features prove to be effectively useless.

I had the idea to turn this into a web-based game, so hopefully I can use AI to implement that game before submission date.

### Harness

For quick scripting, made a harness that loads the puzzle and pickles it for faster use (no need to re-parse .gds every time). Done in `workspace/harness.py`.

`fresh()` creates a new simulator form the cached (global) netlist, calls `sim.reset()`, but also pulses the `rst_n` line to get the circuit into the true reset state (as per puzzle description).

### What drives success:

We know `success` is the goal output, so need to see what drives it. Created `cone.py` to express a given net as a boolean formula over inputs and internal FF outputs. Also has a `walk(net, depth)` function to pretty print the fan in tree.

```py
import sys
sys.path.insert(0, 'workspace')
import cone

print(cone.gate_of('success'))
```

Output:

```
('dfrtp_2_83', 'sky130_fd_sc_hd__dfrtp_2', 'IQ', {'CLK': 'n230', 'D': 'n149', 'Q': 'success', 'RESET_B': 'rst_n', 'VGND': 'VGND', 'VPWR': 'VPWR'})
```

So a single FF, `dfrtp_2_83` with its `D` pin connected to `n149`. Then we need to see what makes that go high:

```py
uv run python -c "
import sys
sys.path.insert(0, 'workspace')
import cone

print(cone.walk('n149', depth=4))"
```

Output:

```
n149 <- a32o_2_5 (a32o_2)  (((A1 & A2) & A3) | (B1 & B2))  {'A1': 'n121', 'A2': 'n98', 'A3': 'n147', 'B1': 'success', 'B2': 'n124'}
 n121 <- inv_2_13 (inv_2)  ~A  {'A': 'n2951'}
  n2951 = <dfrtp_2_50.Q>
 n98 <- and2_2_16 (and2_2)  (A & B)  {'A': 'n200', 'B': 'n97'}
  n200 <- inv_2_6 (inv_2)  ~A  {'A': 'n3499'}
   n3499 = <dfrtp_2_18.Q>
  n97 <- and4_2_3 (and4_2)  (((A & B) & C) & D)  {'A': 'n4097', 'B': 'n3939', 'C': 'n4246', 'D': 'n4123'}
   n4097 = <dfrtp_2_2.Q>
   n3939 = <dfrtp_2_3.Q>
   n4246 = <dfrtp_2_1.Q>
   n4123 <- nor3_2_1 (nor3_2)  ((~A & ~B) & ~C)  {'A': 'n3932', 'B': 'n4021', 'C': 'n4114'}
    n3932 = <dfrtp_2_4.Q>
    n4021 = <dfrtp_2_5.Q>
    n4114 <- or3_2_8 (or3_2)  ((A | B) | C)  {'A': 'n4071', 'B': 'n3924', 'C': 'n3995'}
 n147 <- and4b_2_4 (and4b_2)  (((~A_N & B) & C) & D)  {'A_N': 'n223', 'B': 'n151', 'C': 'n96', 'D': 'n136'}
  n223 = <dfrtp_2_82.Q>
  n151 = <dfrtp_2_61.Q>
  n96 <- and3_2_3 (and3_2)  ((A & B) & C)  {'A': 'n4312', 'B': 'n4318', 'C': 'n4194'}
   n4312 <- and4_2_1 (and4_2)  (((A & B) & C) & D)  {'A': 'n5232', 'B': 'n4296', 'C': 'n5068', 'D': 'n5191'}
    n5232 <- and2b_2_10 (and2b_2)  (~A_N & B)  {'A_N': 'n5251', 'B': 'n5363'}
    n4296 <- and2_2_9 (and2_2)  (A & B)  {'A': 'n5261', 'B': 'n5243'}
    n5068 <- and2b_2_12 (and2b_2)  (~A_N & B)  {'A_N': 'n4784', 'B': 'n5063'}
    n5191 <- and2b_2_11 (and2b_2)  (~A_N & B)  {'A_N': 'n5186', 'B': 'n5163'}
   n4318 <- and4_2_2 (and4_2)  (((A & B) & C) & D)  {'A': 'n4315', 'B': 'n5664', 'C': 'n4313', 'D': 'n4300'}
    n4315 <- and2_2_5 (and2_2)  (A & B)  {'A': 'n5456', 'B': 'n5567'}
    n5664 <- and2b_2_5 (and2b_2)  (~A_N & B)  {'A_N': 'n5479', 'B': 'n5729'}
    n4313 <- and2b_2_7 (and2b_2)  (~A_N & B)  {'A_N': 'n5544', 'B': 'n5254'}
    n4300 <- and2_2_6 (and2_2)  (A & B)  {'A': 'n4335', 'B': 'n5561'}
   n4194 <- and3_2_2 (and3_2)  ((A & B) & C)  {'A': 'n5043', 'B': 'n4924', 'C': 'n4314'}
    n5043 <- and2b_2_15 (and2b_2)  (~A_N & B)  {'A_N': 'n5000', 'B': 'n4916'}
    n4924 <- and2_2_11 (and2_2)  (A & B)  {'A': 'n4920', 'B': 'n5119'}
    n4314 <- and2b_2_16 (and2b_2)  (~A_N & B)  {'A_N': 'n4885', 'B': 'n1522'}
  n136 <- and3_2_17 (and3_2)  ((A & B) & C)  {'A': 'n2972', 'B': 'n2978', 'C': 'n2980'}
   n2972 <- and4_2_7 (and4_2)  (((A & B) & C) & D)  {'A': 'n1038', 'B': 'n944', 'C': 'n853', 'D': 'n962'}
    n1038 <- and2b_2_26 (and2b_2)  (~A_N & B)  {'A_N': 'n1140', 'B': 'n985'}
    n944 <- and2_2_14 (and2_2)  (A & B)  {'A': 'n1026', 'B': 'n946'}
    n853 <- and2b_2_28 (and2b_2)  (~A_N & B)  {'A_N': 'n618', 'B': 'n586'}
    n962 <- and2b_2_27 (and2b_2)  (~A_N & B)  {'A_N': 'n890', 'B': 'n995'}
   n2978 <- and4_2_8 (and4_2)  (((A & B) & C) & D)  {'A': 'n1416', 'B': 'n1447', 'C': 'n37', 'D': 'n2957'}
    n1416 <- and2_2_12 (and2_2)  (A & B)  {'A': 'n1374', 'B': 'n1366'}
    n1447 <- and2b_2_21 (and2b_2)  (~A_N & B)  {'A_N': 'n1239', 'B': 'n1500'}
    n37 <- and2b_2_25 (and2b_2)  (~A_N & B)  {'A_N': 'n1122', 'B': 'n1355'}
    n2957 <- and2_2_13 (and2_2)  (A & B)  {'A': 'n1245', 'B': 'n1337'}
   n2980 <- and3_2_15 (and3_2)  ((A & B) & C)  {'A': 'n628', 'B': 'n846', 'C': 'n837'}
    n628 <- and2b_2_29 (and2b_2)  (~A_N & B)  {'A_N': 'n623', 'B': 'n615'}
    n846 <- and2_2_17 (and2_2)  (A & B)  {'A': 'n788', 'B': 'n784'}
    n837 <- and2b_2_30 (and2b_2)  (~A_N & B)  {'A_N': 'n661', 'B': 'n817'}
 success = <dfrtp_2_83.Q>
 n124 <- nand2b_2_24 (nand2b_2)  (A_N | ~B)  {'A_N': 'n223', 'B': 'n151'}
  n223 = <dfrtp_2_82.Q>
  n151 = <dfrtp_2_61.Q>
None
```

So `n149` is driven by a `a32o_2` with the equation `(((A1 & A2) & A3) | (B1 & B2))`, and each input is: `{'A1': 'n121', 'A2': 'n98', 'A3': 'n147', 'B1': 'success', 'B2': 'n124'}`

We can rewrite this a bit more clearly:

```
D (success) = (n121 & n98 & n147) | (success & n124)
```

So it feeds itself as long as `n124` holds. But the thing that drives it high in the first place is specifically the `(n121 & n98 & n147)` term.

Reading the walk even more:

- `n121` is `inv2(n2951)`, and `n2951` is the output of `dfrtp_2_50`, so `n121` is high when that flop is low.

- `n98 = n200 & n97`. Applying similar logic as above and unravelling, this expands to `n98 = ~Q(dfrtp_2_18) & n97`, so `dfrtp_2_18` must be low, and `n97` must be high. `n97` is a 4-input AND gate, basically ensuring that `dfrtp_2_2`, `dfrtp_2_3`, `dfrtp_2_1`, and `n4123` are all high. `n4123` is a `nor3` that resolves to `dfrtp_2_4 = dfrtp_2_5 = 0` and another `or3` after that. This one is pretty deep

- Looking at `n147`: `n147 = ~n223 & n151 & n96 & n136`. `n223` and `n151` are FF outputs (`dfrtp_2_82`, `dfrtp_2_61`), but `n96` and `n136` are each on top of `and3` or `and4`s if `and2`/`and2b`s.
  - n4312 -> 4 \* 2 = 8 leaves
  - n4318 -> 4 \* 2 = 8 leaves
  - n4194 -> 3 \* 2 = 6 leaves
  - 22 leaves in total

For a tree of pure ANDs, this is probably a big comparator, checking some state property? So current working hypothesis is that `n121` and `n98` are some flags, and `n147` is checking some state property, and if all three are true, then `success` goes high.

22 might be related to the 11-state counter I found a few days ago?

### Reading Leaves:

Added `workspace/leaves.py` to read the leaves of a net's fan in tree programatically since it took me way too long to read the `walk` output manually.

```sh
uv run python workspace/leaves.py n96 n136
n96: 22 leaves
        ~dfrtp_2_22.Q
        dfrtp_2_23.Q
        dfrtp_2_19.Q
        ~dfrtp_2_24.Q
        ~dfrtp_2_31.Q
        dfrtp_2_30.Q
        ~dfrtp_2_28.Q
        dfrtp_2_29.Q
        dfrtp_2_11.Q
        ~dfrtp_2_12.Q
        ~dfrtp_2_7.Q
        dfrtp_2_8.Q
        ~dfrtp_2_13.Q
        dfrtp_2_15.Q
        dfrtp_2_16.Q
        ~dfrtp_2_14.Q
        ~dfrtp_2_39.Q
        dfrtp_2_36.Q
        dfrtp_2_32.Q
        ~dfrtp_2_35.Q
        ~dfrtp_2_45.Q
        dfrtp_2_46.Q
n136: 22 leaves
        ~dfrtp_2_70.Q
        dfrtp_2_71.Q
        dfrtp_2_68.Q
        ~dfrtp_2_69.Q
        ~dfrtp_2_74.Q
        dfrtp_2_75.Q
        ~dfrtp_2_73.Q
        dfrtp_2_72.Q
        dfrtp_2_63.Q
        ~dfrtp_2_62.Q
        ~dfrtp_2_57.Q
        dfrtp_2_56.Q
        ~dfrtp_2_67.Q
        dfrtp_2_66.Q
        dfrtp_2_64.Q
        ~dfrtp_2_65.Q
        ~dfrtp_2_78.Q
        dfrtp_2_76.Q
        dfrtp_2_79.Q
        ~dfrtp_2_80.Q
        ~dfrtp_2_81.Q
        dfrtp_2_84.Q
```

So this is 44 flops total (22 FFs for each of the two deep AND trees). For each of them, exactly 11 of the driving FFs are inverted, and 11 are not. Most of them are pairs, like "22 low and 23 high", or "67 low and 66 high", etc. So possibly these are pairs rather than individual flags. I hope that's the case because it effectively halves the amount of analysis I'm about to do. So maybe each of `n96` and `n136` is checking that 11 2-bit values are all `01` or `10`.

Given how many times 11 or a multiple of it has come up this is almost definitely something related to the circuit's function.

But essentially this target state is now defined as a total of 50 FFs at a specific value. 22 2-bit pair values at `01` or `10`, and 6 other conditions: `dfrtp_2_50 = 0`, `dfrtp_2_18 = 0`, `dfrtp_2_1/2/3 = 1`, and `n4123` resolving to `dfrtp_2_4/5/10 = 0`.
