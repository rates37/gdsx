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
      "This is First Light: seven flops and six gates, about as small as a real standard-cell design gets.",
      "The design has a {{success}} pin and right now it reads low. There is one input, {{I}}, taking one bit per clock cycle. Your job is to work out what to put on it.",
      "You can close the guide at any point and reopen it from the toolbar.",
    ],
  },
  {
    id: "die-2d",
    panel: "die-view",
    title: "The die view, in 2D",
    body: [
      "Let's start with the die itself. This is the real layout of the chip. Drag to pan, wheel to zoom, press {{F}} to fit the die back in frame.",
      "The {{Layers}} box on the left toggles each metal layer and the cell outlines. Turn off {{met2}} and above and you are left with local interconnect and the cells themselves. Turn off the cells too and you have the routing on its own.",
      "Now hover anywhere over a wire. A read-out names the net, and every shape on that net lights up across all the layers it climbs through. That highlight is shared, so the same net lights up in every other panel as well.",
      "Click a wire to keep it selected after the pointer moves away. Right-click gives you the rest: open it in the Cone Walker, find it in the Netlist Browser, or give it a name of your own.",
    ],
    goal: "hover a wire to highlight its net",
    done: () => hasHoveredNet,
  },
  {
    id: "die-3d",
    panel: "die-view",
    title: "…and in 3D",
    body: [
      "Click {{3D}} in the bar above the canvas. Same layout but here in 3D.",
      "Drag the {{Exploded view}} slider to pull the layers apart. Via stacks become obvious once the metals are no longer sitting on top of one another. {{Cross-section}} cuts the die with a plane you can drag through it.",
      "Left-drag orbits. Right-drag, middle-drag or {{Shift}}-drag pans across the die, and {{Reset view}} recovers the camera if you lose your bearings. Click a wire to select its net, then press {{Trace selected net}} and the camera follows that conductor up through the vias.",
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
      "The {{Browser}} sub-tab lists every instance and every net. Thirteen instances here: seven {{dfrtp}} flops and six gates.",
      "Click any row. The detail pane on the right gives you the cell, its Liberty function, and every pin with the net it sits on. Click a net chip in there to open it in the Cone Walker.",
      "Switch to the {{Nets}} tab and set the kind filter to {{flop output}}. Those are the state elements' Q pins, and they are where a design keeps everything it knows.",
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
      "Those extracted names are unmemorable and confusing. {{n143}} is simply whatever the tracer numbered it. Once you work out what something is, you can rename it.",
      "Double-click any net or cell name, here or in the cone walker or the waveform, and give it a name. The {{label}} button in the detail pane does the same job.",
      "A label sits alongside the raw name rather than replacing it. The {{Labels}} sub-tab beside {{Browser}} collects them into a glossary, and the browser's filter box searches your labels as well as the raw names.",
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
      "Registers, not individual flops, are the unit a design is written in. This panel recovers them from Q -> D adjacency, which is to say from which flop feeds which.",
      "Pick the group in the list. You get its width, reset value, enable condition where it has one, and for a small enough cone, the full truth table of any flop's D.",
      "You should see six flops chained one into the next, each Q driving the next D, with the first D fed from {{I}}.",
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
      "You have just read a shift register off the diagram. Now measure it.",
      "With the register still selected, look at the decoding sections in the detail pane. {{▶ walk orbit}} applies a stimulus repeatedly from reset, watches the state sequence, and names the shape it finds. Leave every input at 0 and the answer is {{fixed-point}}, because nothing is being shifted in and so nothing moves. Set {{I=1}} and walk it again and you get {{shift}}, which is the claim you made by eye a moment ago. Where the shape is one the notebook accepts, {{pin as role}} turns it into a claim.",
      "{{▶ infer weights}} recovers what each bit is worth. A weight that could not be observed directly is marked {{by elimination}} in amber, rather than shown as if it had been read off.",
      "The {{ad-hoc group}} box under the register list decodes flops you name yourself, for when the group you care about is not one the recovery pass found.",
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
      "Type {{success}} into the box and press {{walk}}. This is the fan-in cone: what drives this net, what drives that, and so on back towards the inputs.",
      "The walk stops at flops on purpose. Past a flop you are in the previous clock cycle, which is a different question, so crossing one takes a deliberate click on {{step through flop}}. Doing that moves the {{T-1}} marker, and the marker is how you keep track of which cycle you are reading.",
      "A node marked {{···}} is truncated, not a leaf. There is more underneath it that the walk did not expand. This design is too small to produce one but the large puzzles are full of them.",
    ],
    goal: "walk the success cone",
    done: () => q(".cw-tree .cw-row") !== null,
  },
  {
    id: "step-flop",
    panel: "cone-walker",
    title: "Step through the lock",
    body: [
      "{{success}} is a flop output, so its cone is one node deep and the only thing under it is the flop. Click {{step through flop ↦}}.",
      "You are now one cycle earlier, looking at that flop's D input, with the {{T-1}} badge to indicate it. The gate is an {{o21ai}}, function {{((~A1 & ~A2) | ~B1)}}, and its {{B1}} input is {{success}} itself coming back round through an inverter. That self-feed holds the lock once it is set.",
      "The other two inputs are the comparator. Expand them and you get a pair of {{nand2}} cells over an {{and2}} and a {{nor2}}, with the six shift-register flops as their leaves. You can read this one by eye. On a real design you will not be able to, which is what the next step is for.",
    ],
    goal: "step through the success flop",
    done: () => q(".cw-cycle-badge") !== null,
  },
  {
    id: "flatten",
    panel: "cone-walker",
    title: "Flatten the tree and read the answer off it",
    body: [
      "Click the {{o21ai}}'s own row to make it the {{focus}}, then press {{flatten AND/OR tree}}. Nothing comes back forced. You get one choice with two options: the inverter's output at 0, or both {{nand2}} outputs at 0. That is the self-hold and the comparator, side by side. A lock that is already set stays set whatever the comparator says, so neither option is forced, and the result is shown as a genuine choice.",
      "Take the comparator option: Both of those outputs have to be {{0}}, so flip the toggle beside the flatten button from {{→ 1}} to {{→ 0}} and flatten each of them in turn. Flattening for 1 and flattening for 0 are different questions.",
      "The first forces two flops, the second forces four. Six leaves between them, every one forced, with no choices left over: {{dfrtp_2_1.Q}} through {{dfrtp_2_6.Q}}, each pinned to a 1 or a 0. That is the entire unlock condition, and you have it without having run the design once.",
      "Forced rows and choice rows are kept visually apart for a reason. Collapse the two together and \"must\" quietly becomes \"might\".",
    ],
    goal: "flatten a cone",
    done: () => q(".cw-flatten-panel:not([hidden]) .rq-leaf") !== null,
  },
  {
    id: "requirements",
    panel: "cone-walker",
    title: "Forced, chosen, and satisfied right now",
    body: [
      "Look at the flatten result. Each forced row carries a live ✓ or ✗ against whatever your current sequence produces {{at the cursor cycle}}, and moving the waveform cursor moves the column with it. A ✗ is the distance between where the design is now and where it has to be.",
      "{{pin}} turns any forced flop row into a notebook requirement claim, which is how a derivation becomes something the game can score.",
      "Now flatten {{success}} itself for contrast. You get one row, {{dfrtp_2_7.Q == 1}}, which is exactly right: {{success}} is that flop's output, and justification stops at flops for the same reason the walk does. Not a dead end, just a reminder of which cycle you are standing in.",
    ],
    goal: "pin a requirement to the notebook",
    done: () => q(".rq-claim-btn[disabled]") !== null,
  },
  {
    id: "waveform",
    panel: "waveform",
    title: "Watch it happen",
    body: [
      "You have the condition, now watch the design try to meet it.",
      "Type in the search box to add any net or flop to the watch list. {{O}} is offered as a bus, {{O[5:0]}}, with a radix that can be changed. Drag rows to reorder them, and click a cycle to move the cursor.",
      "The cursor is shared across panels, so moving it here moves the ✓/✗ column on the flatten result back in the Cone Walker. Add {{success}} and {{O}} now, because you will want both for the next step.",
    ],
    goal: "add a signal to the waveform",
    done: () => qq(".wave-row") > 2,
  },
  {
    id: "sequence",
    panel: "sequence-editor",
    title: "Drive the input",
    body: [
      "One track per input port, one cell per cycle. Click to toggle a bit, drag to paint a run of them. With {{auto-run}} on, the trace recomputes live as you edit.",
      "Paint the six bits you derived, starting at cycle 0. Mind which end is which. {{dfrtp_2_1}} takes its D straight from {{I}}, so it holds the most recent bit and {{dfrtp_2_6}} holds the oldest, which means the value you want in {{dfrtp_2_6}} is the one you drive first. This particular word reads the same in both directions, so you cannot get it wrong here.",
      "Watch {{O}} in the waveform fill up as the word shifts in. The readout on the right tells you whether {{success}} latched, and at which cycle. Once it does, you have solved it!",
    ],
    goal: "make success latch",
    done: () => q(".seq-result.latched") !== null,
  },
  {
    id: "submit",
    title: "Hand in the answer",
    body: [
      "{{submit}}, in the toolbar, is what actually resolves a puzzle. It is separate from the notebook, which records how you got there.",
      "Open it. For a sequence puzzle like this one you get one field per input port, each prefilled from what you painted in the Sequence Editor, so the six bits you just proved out are already sitting there.",
      "Press {{check}}. {{✓ accepted}} is the real win condition. A rejection comes back with what was observed instead.",
    ],
    goal: "submit an answer",
    done: () => q(".gdsx-submit-verdict.gdsx-submit-ok") !== null,
  },
  {
    id: "experiments",
    panel: "experiments",
    title: "Experiments: the measurement you would have run instead",
    body: [
      "That was the easy route, and it only worked because the cone was small enough to read. The rest of the walkthrough covers what you do when it is not.",
      "The Experiment Runner sweeps a perturbation over a baseline and shows you what moved. Pick {{single-pulse sweep}}, baseline {{idle}}, and press {{Run}}. Every row is one run with a single pulse at one cycle, every column is a watched element, and a mark means that pulse changed that element's final value.",
      "On a shift register the result is a diagonal, and that diagonal is the bit-to-cycle map. This is how you crack a design whose logic you cannot read. {{record as evidence}} attaches the run to your notebook.",
    ],
    goal: "run an experiment",
    done: () => q(".xp-body:not([hidden]) .xp-canvas") !== null && q(".xp-summary *") !== null,
  },
  {
    id: "sensitivity",
    panel: "experiments",
    title: "The same sweep, read the other way",
    body: [
      "Tick {{elements down the side}} in the toolbar. The matrix flips, putting watched elements down the side and runs across the top.",
      "Both orientations flag the same two self-checks as warnings: an element that no cycle can move, and a cycle that moves nothing. Either one means your grouping or your window is wrong, and catching that straight away beats a matrix that merely looks plausible. Press {{→ constraints}} on a summary row to send what you measured to the Constraints drawer.",
    ],
    goal: "flip the matrix round",
    done: () => (q(".xp-transpose") as HTMLInputElement | null)?.checked === true,
  },
  {
    id: "constraints",
    panel: "experiments",
    title: "Constraints: bookkeeping, not solving",
    body: [
      "Open the {{Constraints}} drawer at the foot of this panel. Rows you have measured, each of the form “exactly k of these cycles”, collect there, along with a count of how many assignments satisfy all of them and a warning when the system is still under-constrained.",
      "The points are awarded in deriving the rows. On a large puzzle this is what saves you doing the arithmetic on paper.",
    ],
    goal: "open the Constraints drawer",
    done: () => q(".pd-drawer.on .cs-rows") !== null,
  },
  {
    id: "notebook",
    panel: "notebook",
    title: "The notebook is what is actually scored",
    body: [
      "Solving a puzzle closes it. The notebook is where you show your working, and it is what carries most of the score (you aren't just judged on how fast you get the correct answer, but also on the amount of reasoning/experimenting you did).",
      "Press {{+ new claim}}. Pick a claim type, fill the blanks against real nets, and the claim is checked against the netlist: structural claims instantly, functional ones by enumerating the cone.",
      "The strength of a verdict is as prominent as the verdict itself. {{PROVEN}} is green and carries the number of cases covered. {{LIKELY}} is amber and reads “no counterexample in N vectors”, never “proven”.",
      "A disproof scores too, and stays in the notebook struck through and timestamped. {{export write-up}} turns the whole notebook into a Markdown account of the investigation.",
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
      "Write a small model, in JavaScript or Python, that predicts {{success}} from the input bits. For this design it comes down to one comparison against a six-bit word.",
      "Press {{Validate}}. The game generates vectors, runs your model and the real gate tape side by side, and reports the agreement rate along with the first divergence.",
      "100% over N vectors earns the {{model validated}} badge, which is worth more than solving the sequence. On the hard puzzles it can be the only accepted answer, because explaining the design is the win condition.",
    ],
    goal: "validate a model",
    done: () => q(".mb-report *") !== null,
  },
  {
    id: "repl",
    panel: "repl",
    title: "And when the panels are not enough",
    body: [
      "The Python panel runs the {{gdsx}} Python library in your browser, with this design already bound: {{nl}} is the netlist, {{graph}} its connectivity, {{sim}} a simulator, {{design}} the object the panels themselves call, and {{api}} the module. It is a session rather than a run of one-shot evaluations, so names you bind stay bound. {{Enter}} runs, {{Shift+Enter}} adds a line, and {{reset()}} restores those bindings if you overwrite one.",
      "Try {{design.registers(ordered=True)}}, or step across the lock flop the way the Cone Walker does: {{graph.cone({graph.d_pin('dfrtp_2_7')})}}. Mind the braces, they are load-bearing. {{cone}} takes a set of nets, so a bare net name gets iterated one letter at a time and you get an empty set back with no warning.",
      "The output is plain rather than pretty. A dataclass the library knows how to flatten prints as JSON, and everything else prints as its {{repr()}}. Every panel carries a {{ { } }} button showing the exact call behind what it displays, ready to paste in here.",
    ],
    goal: "run something in the REPL",
    done: () => q(".repl-entry") !== null,
  },
  {
    id: "done",
    title: "That is the whole toolkit",
    body: [
      "Observe, hypothesise, test, record, model, solve. You have now used every panel the game has, on a design small enough to have been decoded by hand, without any of these tools.",
      "The larger puzzles differ in scale and purpose. The tools you have are the same though.",
      "Pick another level from the toolbar whenever you are ready. This walkthrough stays available from the {{guide}} button if you want to come back to any of it.",
    ],
  },
];