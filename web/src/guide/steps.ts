// The guided walkthrough for "First Light", puzzle 0.
//
// The steps are *content*, kept apart from the machinery that shows them
// (`guide.ts`) so that adding, cutting or reordering a step is an edit to
// this file alone.
//
// Three rules the steps follow, and they are what keep this from reading as
// a wizard that plays the game for you:
//
//  1. **Every step says what to click and what you should see, then says why
//     it matters.** The player is an engineer; the mechanism is the
//     interesting part, not the button.
//  2. **Nothing is ever blocked.** `done` is a live check that lights up when
//     you have done the thing, never a gate: Next always works, and a player
//     who already knows a panel can skip through it.
//  3. **The answer is derived in front of you, not given.** Step by step the
//     walkthrough reaches the six forced leaves and reads the word off them.
//     The last step is the player typing it in.
//
// The checks are deliberately DOM-shaped rather than reaching into panel
// internals: a panel is free to change how it works as long as it still
// shows what it shows, and a check that goes stale degrades to "the tick
// never lights", not to a broken tutorial.

// Explicit `.ts` extensions (as in requirements-panel.ts and friends) so this
// module is importable by scripts/test-guide.mjs under Node's ESM loader,
// which does not do Vite's extensionless resolution.
import { labels } from "../store/labels.ts";
import { highlightBus } from "../store/highlight.ts";

// Hovering a net is transient -- it is gone again the moment the pointer
// leaves -- so a poll would almost always miss it. Latch it instead: the
// question the step asks is "have you tried this yet", not "are you doing it
// right now".
let hasHoveredNet = false;
highlightBus.subscribe((sel) => {
  if (sel) hasHoveredNet = true;
});

export interface GuideStep {
  /** Stable id -- progress is stored against it, so reordering steps does
   *  not silently teleport a returning player. */
  id: string;
  /** Panel to bring to the front when the step opens. */
  panel?: string;
  /** Sub-tab within that panel to open, once a panel hosts more than one.
   *  Checked against `PANEL_SECTIONS` in scripts/test-guide.mjs, so a step
   *  naming a section that does not exist is a test failure rather than a
   *  step that quietly lands on whichever sub-tab was last used. */
  section?: string;
  title: string;
  /** Paragraphs. Plain text; `code` spans are written as {{…}} and rendered
   *  as `<code>` so the steps never carry raw HTML. */
  body: string[];
  /** What "you have done it" looks like on the page. Absent = nothing to do
   *  but read. */
  done?: () => boolean;
  /** One line under the body, shown greyed: what the tick is watching for. */
  goal?: string;
}

const q = (selector: string): Element | null => document.querySelector(selector);
const qq = (selector: string): number => document.querySelectorAll(selector).length;

export const GUIDE_PUZZLE_ID = "0-first-light";

export const STEPS: GuideStep[] = [
  {
    id: "intro",
    panel: "die-view",
    title: "You have a die and no documentation",
    body: [
      "This is First Light: seven flops and six gates, which is about as small as a real standard-cell design gets. Everything you learn here works unchanged on the 728-instance puzzles.",
      "The design has a {{success}} pin and it is low. There is one input, {{I}}, one bit per clock cycle. Your job is to work out what to put on it.",
      "Nothing here is blocked. Next always works, the tick on the left just lights up when you have done the thing being described, and you can close the guide at any time and come back to it from the toolbar.",
    ],
  },
  {
    id: "die-2d",
    panel: "die-view",
    title: "The die view, in 2D",
    body: [
      "This is the actual layout, drawn from the extracted geometry — not a diagram of it. Drag to pan, wheel to zoom, press {{F}} to fit the die back in frame.",
      "The {{Layers}} box on the left toggles each metal layer and the cell outlines. Turn {{met2}} and above off and you are looking at local interconnect and the cells themselves; turn the cells off and you see routing alone.",
      "Hover anywhere over a wire. A read-out names the net, and everything on it lights up across every layer it climbs through — that is the same net highlight every other panel drives.",
      "Click the wire to keep it selected once the pointer moves away, and right-click it for the rest: open it in the Cone Walker, find it in the Netlist Browser, or give it a name of your own.",
    ],
    goal: "hover a wire to highlight its net",
    done: () => hasHoveredNet,
  },
  {
    id: "die-3d",
    panel: "die-view",
    title: "…and in 3D",
    body: [
      "Click {{3D}} in the bar above the canvas. The same layout, extruded through the real sky130 metal stack.",
      "Drag the {{Exploded view}} slider to pull the layers apart — via stacks become obvious once the metals are not sitting on top of each other. {{Cross-section}} cuts the die with a plane you can drag through it.",
      "Left-drag orbits; right-drag, middle-drag or {{Shift}}-drag pans across the die, and {{Reset view}} puts it back if you lose it. Click a wire to select its net, then {{Trace selected net}}, and the camera follows the conductor up through the vias. Switch back to {{2D}} when you have had a look; the rest of the walkthrough uses the flat view.",
    ],
    goal: "switch the die view to 3D",
    done: () => q('.die-mode-seg button[data-mode="3d"].on') !== null,
  },
  {
    id: "netlist",
    panel: "netlist",
    section: "browser",
    title: "What the extractor actually found",
    body: [
      "The {{Browser}} sub-tab lists every instance and every net recovered from that geometry. Thirteen instances: seven {{dfrtp}} flops and six gates.",
      "Click any row. The detail pane on the right gives you the cell, its Liberty function, and every pin with the net it is on. Click a net chip in there and it opens in the Cone Walker.",
      "Switch to the {{Nets}} tab and set the kind filter to {{flop output}} — those are the state elements' Q pins, and they are where a design keeps everything it knows.",
    ],
    goal: "select a row in the netlist browser",
    done: () => q(".netlist-detail .detail-box") !== null,
  },
  {
    id: "labels",
    panel: "netlist",
    section: "labels",
    title: "Name things as you learn them",
    body: [
      "The extracted names are honest and unmemorable: {{n143}} is whatever the tracer numbered it. When you work out what something is, say so.",
      "Double-click any net or cell name — here, in the cone walker, in the waveform — and give it a name you will recognise. Or use the {{label}} button in the detail pane.",
      "Labels are yours, not renames: the raw name stays visible beside them and everything underneath still uses it. This {{Labels}} sub-tab, beside {{Browser}}, is the glossary you end up with — and the browser's filter box searches labels too.",
    ],
    goal: "label a net or a cell",
    done: () => labels.size > 0,
  },
  {
    id: "registers",
    panel: "register-inspector",
    section: "registers",
    title: "Group the flops before reading the logic",
    body: [
      "Registers, not flops, are the unit a design is written in. This panel recovers them from Q→D adjacency: which flop feeds which.",
      "Pick the group in the list. You get its width, its reset value, its enable condition where there is one, and — for a small cone — the full truth table of any flop's D.",
      "You should see six flops chained one into the next, each Q driving the next D, with the first one's D on {{I}}. That is a shift register: a six-bit window onto the last six things you put on the input.",
    ],
    goal: "open a register group",
    done: () => q(".ri-detail-body .ri-section") !== null,
  },
  {
    id: "decoder",
    panel: "register-inspector",
    section: "registers",
    title: "Confirm it rather than assume it",
    body: [
      "With the register still selected, look at the decoding sections in the detail pane. {{▶ walk orbit}} applies a stimulus repeatedly from reset and watches the state sequence, then names the shape it found. Leave every input at 0 and you get {{fixed-point}} — nothing is being shifted in, so nothing moves. Set {{I=1}} first and walk it again: now it is a {{shift}}, which is the claim you made by eye a moment ago, measured. Where the shape is one the notebook accepts, {{pin as role}} turns it into a claim.",
      "{{▶ infer weights}} recovers what each bit is worth. Where a weight could not be observed directly it says {{by elimination}} in amber rather than presenting a guess as a reading. Take that distinction seriously; it is the difference between knowing and assuming, and it is the whole game.",
      "The {{ad-hoc group}} box under the register list decodes flops you name yourself — for when the interesting group is one the recovery pass did not find.",
    ],
    goal: "walk an orbit or infer weights",
    done: () => q(".rd-orbit-kind") !== null || q(".rd-weight-line") !== null,
  },
  // There was a "Find the lock" step here, routing to the Registers panel's
  // Sticky Flops section. It is gone because this design has no sticky flop
  // to find: `sequential.sticky` recognises feedback that arrives the same way
  // round it left, and First Light's lock holds itself through an inverter, so
  // the section lists nothing. The step described a flop that was not there
  // and its tick watched for a row that could never be drawn.
  //
  // The section is not taught anywhere else in the walkthrough as a result.
  // That is deliberate -- a step whose panel is empty on the only design the
  // walkthrough runs on teaches nothing -- and it is the player manual's job
  // to cover it.
  {
    id: "cone",
    panel: "cone-walker",
    title: "Walk back from success",
    body: [
      "Type {{success}} into the box and press {{walk}}. This is the fan-in cone: what drives this net, and what drives that, back towards the inputs.",
      "The walk stops at flops on purpose. Past a flop you are in the previous clock cycle, which is a different question — so crossing one is a deliberate click, {{step through flop}}, and it moves the {{T-1}} marker so you always know which cycle you are looking at.",
      "A node marked {{···}} is truncated, not a leaf: there is more below and the walk stopped. Reading a truncated node as a primary input is the single most expensive mistake in this kind of work, so the panel refuses to let the two look alike.",
    ],
    goal: "walk the success cone",
    done: () => q(".cw-tree .cw-row") !== null,
  },
  {
    id: "step-flop",
    panel: "cone-walker",
    title: "Step through the lock",
    body: [
      "{{success}} is a flop output, so the cone is one node deep and the only thing under it is the flop itself. Click {{step through flop ↦}}.",
      "You are now one cycle earlier, looking at that flop's D input, and the {{T-1}} badge says so. It is an {{or2}}: one input is the comparator, the other is {{success}} itself. That self-feed is the stickiness, seen in the gates.",
      "Expand the comparator side. It is a small tree of {{and2}} cells with one {{nor2}} in it, and its leaves are the six shift-register flops.",
    ],
    goal: "step through the success flop",
    done: () => q(".cw-cycle-badge") !== null,
  },
  {
    id: "flatten",
    panel: "cone-walker",
    title: "Flatten the tree — the answer falls out",
    body: [
      "Click the row for the top {{and2}} of that comparator tree to make it the {{focus}}, then press {{flatten AND/OR tree}}.",
      "Six leaves, every one of them forced, no choices left over: {{dfrtp_2_1.Q}} through {{dfrtp_2_6.Q}}, each pinned to a 1 or a 0. That is the entire unlock condition, and you have it without running the design once.",
      "Now flatten the {{or2}} above it instead. You get two options rather than forced leaves — {{the comparator fires}}, or {{success is already set}} — because with a sticky latch that genuinely is a choice. Forced and choice are drawn apart on purpose; collapsing one into the other is a lie.",
    ],
    goal: "flatten a cone",
    done: () => q(".cw-flatten-panel:not([hidden]) .rq-leaf") !== null,
  },
  {
    id: "requirements",
    panel: "cone-walker",
    title: "Forced, chosen, and satisfied right now",
    body: [
      "Look at the flatten result again. Each forced row carries a live ✓ or ✗ against whatever your current sequence produces {{at the cursor cycle}} — move the waveform cursor and the column follows it. A ✗ is not a failure, it is the gap between where the design is and where it has to be.",
      "{{pin}} turns any forced flop row into a notebook requirement claim, which is how a derivation becomes something the game can score.",
      "Now flatten {{success}} itself for contrast: one row, {{dfrtp_2_7.Q == 1}}. Of course — {{success}} *is* that flop's output, and justification stops at flops exactly as the walk does. Not a dead end; a reminder of which cycle you are standing in.",
    ],
    goal: "pin a requirement to the notebook",
    done: () => q(".rq-claim-btn[disabled]") !== null,
  },
  {
    id: "waveform",
    panel: "waveform",
    title: "Watch it happen",
    body: [
      "The waveform runs on a compiled gate tape in the browser, so scrubbing is instant — there is no round trip to Python here.",
      "Type in the search box to add any net or flop to the watch list; {{O}} is offered as a bus, {{O[5:0]}}, with a radix you can change. Drag rows to reorder, click a cycle to move the cursor.",
      "The cursor is shared: move it here and the flatten result's ✓/✗ column back in the Cone Walker follows it. Add {{success}} and {{O}} now — you will want them both in front of you for the next step.",
    ],
    goal: "add a signal to the waveform",
    done: () => qq(".wave-row") > 2,
  },
  {
    id: "sequence",
    panel: "sequence-editor",
    title: "Drive the input",
    body: [
      "One track per input port, one cell per cycle. Click to toggle a bit, drag to paint a run of them. With {{auto-run}} on, the trace recomputes on every edit.",
      "Paint the six bits you derived, starting at cycle 0, first-driven bit first. Watch {{O}} in the waveform fill up as the word shifts in.",
      "The readout on the right says whether {{success}} latched and at which cycle. When it does, you have solved it — the tutorial's design is small enough to brute-force in 64 tries, and you did it in one.",
    ],
    goal: "make success latch",
    done: () => q(".seq-result.latched") !== null,
  },
  {
    id: "submit",
    title: "Hand in the answer",
    body: [
      "{{submit}}, in the toolbar, is what actually resolves the puzzle — separate from the notebook, which records how you got there. It only appears when a puzzle can be checked, and First Light can.",
      "Open it. For a sequence puzzle like this one it offers one field per input port, and prefills each from whatever you already painted in the Sequence Editor — so the six bits you just proved out are already sitting there.",
      "Press {{check}}. {{✓ accepted}} is the actual win condition; a rejection shows what was observed instead of just a buzzer, so a near miss tells you how near.",
    ],
    goal: "submit an answer",
    done: () => q(".gdsx-submit-verdict.gdsx-submit-ok") !== null,
  },
  {
    id: "experiments",
    panel: "experiments",
    title: "Experiments: the measurement you would have run instead",
    body: [
      "Suppose the cone had been too big to read. The Experiment Runner sweeps a perturbation over a baseline and shows you what moved.",
      "Pick {{single-pulse sweep}}, baseline {{idle}}, and press {{Run}}. Every row is one run with a single pulse at one cycle; every column is a watched element; a mark means that pulse changed that element's final value.",
      "On a shift register the result is a diagonal, and the diagonal *is* the bit-to-cycle map. That is how you crack a design whose logic you cannot read. {{record as evidence}} attaches the run to your notebook.",
    ],
    goal: "run an experiment",
    done: () => q(".xp-body:not([hidden]) .xp-canvas") !== null && q(".xp-summary *") !== null,
  },
  {
    id: "sensitivity",
    panel: "experiments",
    title: "The same sweep, read the other way",
    body: [
      "Tick {{elements down the side}} in the toolbar. The matrix flips: watched elements down the side, runs across the top — the slot map, rather than the run log.",
      "Nothing re-runs. It is the same result you already have, indexed the other way round, which is worth knowing because on a shift register the diagonal only becomes obvious in this orientation.",
      "Both orientations surface the same two self-checks as warnings: an element no cycle can move, or a cycle that moves nothing. Either means your grouping or your window is wrong, and finding that out immediately is worth more than a matrix that merely looks plausible. Press {{→ constraints}} on a summary row to send what you measured to the Constraints drawer.",
    ],
    goal: "flip the matrix round",
    done: () => (q(".xp-transpose") as HTMLInputElement | null)?.checked === true,
  },
  {
    id: "constraints",
    panel: "experiments",
    title: "Constraints: bookkeeping, not solving",
    body: [
      "Open the {{Constraints}} drawer at the foot of this panel. Rows you have measured — “exactly k of these cycles” — accumulate there, and it says how many assignments satisfy all of them, and whether the system is under-constrained.",
      "It scores nothing, and it says so itself. The points are in deriving the rows; on a large puzzle this is what stops you doing that arithmetic on paper.",
      "It lives here, next to the sweep that feeds it, rather than in a tab of its own — and it does not even load until you open it, so it never delays a sweep.",
    ],
    goal: "open the Constraints drawer",
    done: () => q(".pd-drawer.on .cs-rows") !== null,
  },
  {
    id: "notebook",
    panel: "notebook",
    title: "The notebook is what is actually scored",
    body: [
      "Press {{+ new claim}}. Pick a claim type, fill the blanks against real nets, and the game checks it against the netlist — structural claims instantly, functional ones by enumerating the cone.",
      "The verdict's strength is as prominent as the verdict. {{PROVEN}} is green with the number of cases it covered; {{LIKELY}} is amber and says “no counterexample in N vectors” and never uses the word proven. They are different claims about the world.",
      "A disproof scores too, and stays in the notebook struck through and timestamped. Discovering your earlier conclusion was wrong is the best thing that happens in a real session. {{export write-up}} turns the whole notebook into a Markdown account of the investigation.",
    ],
    goal: "record a claim",
    //: `.nb-row` is what the list actually emits -- this looked for
    //: `.nb-claim`, which the panel has never rendered, so only the open-form
    //: fallback could ever fire. Evidence rows share `.nb-row`, hence the
    //: exclusion.
    done: () =>
      q(".nb-list .nb-row:not(.nb-evidence-row)") !== null ||
      q(".nb-form:not([hidden])") !== null,
  },
  {
    id: "model",
    panel: "model-builder",
    title: "Model builder: prove you understood it",
    body: [
      "Write a small model — JavaScript or Python — that predicts {{success}} from the input bits. For this design it is one comparison against a six-bit word.",
      "Press {{Validate}} and the game generates vectors, runs your model and the real gate tape side by side, and reports agreement plus the first divergence, one click from loading that vector into the waveform.",
      "100% over N vectors earns the {{model validated}} badge, which is worth more than solving. On the hard puzzles it can be the only accepted answer: explaining the design *is* the win condition.",
    ],
    goal: "validate a model",
    done: () => q(".mb-report *") !== null,
  },
  {
    id: "repl",
    panel: "repl",
    title: "And when the panels are not enough",
    body: [
      "The Python drawer runs the real {{gdsx}} library in your browser, with this design already bound to {{nl}}, {{design}} and {{graph}}. Everything the panels do is a call you can make yourself.",
      "Try {{design.registers(ordered=True)}}, or {{graph.cone('success')}}. Returning a netlist renders a table; returning a trace renders a waveform.",
      "Every panel also has a {{{ }}} button that shows the exact call behind what it is currently displaying, copyable straight into here. The GUI is a convenience over the library, never a wall around it.",
    ],
    goal: "run something in the REPL",
    done: () => q(".repl-entry") !== null,
  },
  {
    id: "done",
    title: "That is the whole toolkit",
    body: [
      "Observe, hypothesise, test, record, model, solve. You have now used every panel the game has on a design small enough to hold in your head.",
      "The larger puzzles differ in scale, not in kind: the same cone walker, the same sweeps, the same distinction between what you proved and what you merely failed to disprove.",
      "Pick another level from the toolbar whenever you are ready. You can reopen this walkthrough from the {{guide}} button at any time.",
    ],
  },
];