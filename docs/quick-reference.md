# Quick reference

This is a quick reference for the game, intended as a small summary of its features. Proper documentation can be found in the [manual](manual.md).

## Toolbar, left to right

Lets you access the main tools/views in the game + navigation.

| Item             | Function                                                                               |
| ---------------- | -------------------------------------------------------------------------------------- |
| `< levels`       | Back to the main menu / level select                                                   |
| View menu        | Open the Die View, Netlist, Waveform tabs                                              |
| Analyse menu     | Open the Cone Walker, Registers, Python Shell tabs                                     |
| Experiments menu | Open the sequence editor, experiments, and model builder tabs                          |
| level dropdown   | Quickly switch between different levels (progress is saved)                            |
| objective        | States the goal of the current level                                                   |
| `notebook`       | Opens the notebook (the scored panel)                                                  |
| `submit`         | Opens a form to submit a solution                                                      |
| `hints`          | Get tiered hints for the current level. Each hint used reduces final score             |
| `guide`          | Opens the guided walkthrough of the tutorial level. Progress of current level is saved |
| `reset layout`   | Resets the panel layout to default arrangement                                         |

## Panels

Each panel can be dragged around the window, snapped to edges, closed, and re-sized. Has a similar feel to the tabs/panels in VS code / other IDEs.

| Panel           | Function                                                                                                               |
| --------------- | ---------------------------------------------------------------------------------------------------------------------- |
| Die View        | Shows the visual layout of the chip in 2D and 3D, like [gds-viewer.tinytapeout](https://gds-viewer.tinytapeout.com/)   |
| Netlist         | Browse instances and nets                                                                                              |
| Cone Walker     | Walk a net's fan-in and fan-out, flatten a target value into forced/choice leaves.                                     |
| Registers       | Recovered register grouping (might be inaccurate), truth tables, etc.                                                  |
| Waveform        | Watch signals over the whole run, based on the current values in the sequence editor                                   |
| Sequence Editor | Choose the sequence to apply for each input in the simulation                                                          |
| Experiments     | Choose perturbations and observe the behaviour of the design                                                           |
| Model builder   | Write a model of your predictions and assert it against the actual design                                              |
| Python          | An interactive Python shell, includes objects that store the netlist as a graph for manipulation/programmatic analysis |
| Notebook        | Tracks your scored claims and records evidence                                                                         |

## Die View

A visual representation of the chip layout in 2D and 3D. Hover over nets to highlight them. Left click to select (keeps it highlighted), and right click to open context menu with more options like open in cone walker.

Controls cheat sheet:

| Control                             | Function                                 |
| ----------------------------------- | ---------------------------------------- |
| drag (2D)                           | Pan view                                 |
| drag (3D)                           | Rotates/Orbits                           |
| F                                   | fits the die to the current view         |
| scroll wheel                        | zooms in/out                             |
| right-drag, middle-drag, shift-drag | Pan in 3D                                |
| debug info                          | shows fps and draw stats                 |
| click net                           | selects the net, opens it in cone walker |
| right click net                     | context menu                             |

## Netlist panel

Shows cell instances and nets. Internal (non-port) nets are assigned unique names like `n129`, `n142`, etc.

Clicking an instance opens it in a right-hand panel in this view, showing function (e.g., `Y = (~A | ~B)`), its connections, and the base cell (e.g., cell: `sky130_fd_sc_hd__nand2_2` and base: `nand2 generic: NAND2`).

The "label" button allows you to assign a custom name to the net/instance which will be used throughout the rest of the panels. Double clicking a net or instance name (where it is shown in blue, such as on the right panel) will also open a form to add a label to it.

"Open in cone walker" does exactly what it sounds like.

## Cone Walker

Explore everything that is feeding the net, or everything that the net is feeding into. Fan-in cone is the set of its drivers, their drivers, and so on, until reaching the inputs or a FF. Fan-out cone is the set of everything that depends on the net's value including the FFs it drives.

When reading the cone, it shows in a heirarchical view, each indent represents another level of depth. A row marked as a leaf means the walk has stopped. "primary input" means it reached a port for the design. "Flop Q" means it reached a FFs' output. "Constant 0/1" means the net is always that constant value.

Clicking "step through flop" walks the cone further back, treating the FF point as the FF's D-input, it gets labelled a `T-1`. Stepping through another layer will label `T-2` and so on.

## Registers panel

The registers panel shows recovered register groupings, based on automatic analysis of the design's netlist. It may be flawed and inaccurate, and likely to change in the future. It is a WIP currently (might either remove or replace entirely in the future), but still can be useful (on rare occasion).

Also displays truth tables for the grouped register (again, the view of this is a WIP, and not presented amazingly).

## Waveform

It displays the signal waveforms. Recommend dragging this panel to the bottom and then opening the sequence editor to edit input sequence and see output sequence at the same time.

Shows all input and output ports, but also lets you add any internal net as well. Really helpful for debugging and understanding small subsets of the netlist.

## Sequence Editor

Lets you edit the sequence of inputs that are applied to the design (simulation outputs shown in waveform panel).

## Experiments

Allows you to perturb the design and observe its behaviour. Pick a recipe, baseline, and watch set, then click run. The output is a matrix of one run per row, one column per watched signal, and a mark where that run changed that element.

- Baseline: the unperturbed design, based on what is selected in the sequence editor
- Recipes:
  - single-pulse sweep: one extra pulse at each cycle in the run
  - gap sweep: puts a pair of pulses at every starting cycle and every spacing
  - bit-flip sensitivity: flips each cycle of the currently set sequence (from the sequence editor)
  - reset-value scan: doesn't change anything, just shows what each element holds at reset
- Window/watch set:
  - `cycles ... to ...` bounds the sweep to a subset of the run's clock cycles
  - `watch`: pick which signals to watch
- Result: a grid/matrix of runs/signals, showing where changes occurred. Hover over cell for info

## Model Builder

Very experimental feature. Write a small program (python or JS) to predict what the design does, and assert it against the actual design to see if correct or not. 


## Notebook

Experimental. The notebook is how the scoring system works in the game. You make a claim about the design (a structured assertion that the game can check). Types of claims:

* Structural: claim a net is driven by a particular kind of cell

* Support: claim a FF's D input depends on a set of nets (basically you need to describe the input cone)

* Function: claim a FF's D input equals a boolean expression

* Role: claim a group of flops is a register/counter/shift register, saturating counter, etc.

* Invariant: claim a net holds a value whenever some condition is true

* Requirement: claim that for an output to hold a value, some other FF must a value

* Timing: claim a net does something on particular cycles under the sequence it's driven at (editing sequence later doesn't change what has already been checked)

Results can be proven in `N` cases, proven structurally, disproven, unknown.

## All pointer/keyboard gestures

See [the manual](manual.md) for what each of these means and how to use them together.
