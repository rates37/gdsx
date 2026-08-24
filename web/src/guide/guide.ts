// The guided walkthrough: a floating card that steps a player through the
// tutorial puzzle, one panel at a time.
//
// Deliberately *not* a wizard. It never blocks a control, never takes an
// action on the player's behalf, and never gates Next behind a check. What
// it does is bring the right panel to the front, say what to click and what
// you should see, and light a tick when you have done it. A player who
// already knows a panel skips through it in two clicks; a player who does
// not gets told what the panel is for, which is the part a tooltip cannot
// carry.
//
// Progress is per step id, latched: a step that has been satisfied once
// stays satisfied, so switching tabs (which detaches a panel's DOM, and with
// it whatever the check was looking at) cannot un-tick it.

import { STEPS, type GuideStep } from "./steps";
import type { Workspace } from "../workspace/workspace";

const STATE_KEY = "gdsx.guide.state.v1";
/** Set by the toolbar just before it navigates to the tutorial puzzle from
 *  somewhere else, and consumed on the next load. */
const AUTOSTART_KEY = "gdsx.guide.autostart";
const POLL_MS = 500;

interface SavedState {
  active: boolean;
  index: number;
  collapsed: boolean;
  satisfied: string[];
}

function loadState(): SavedState {
  const fallback: SavedState = { active: false, index: 0, collapsed: false, satisfied: [] };
  try {
    const raw = localStorage.getItem(STATE_KEY);
    if (!raw) return fallback;
    const saved = JSON.parse(raw) as Partial<SavedState>;
    return {
      active: saved.active === true,
      index: typeof saved.index === "number" ? saved.index : 0,
      collapsed: saved.collapsed === true,
      satisfied: Array.isArray(saved.satisfied) ? saved.satisfied : [],
    };
  } catch {
    return fallback;
  }
}

/** Marks the guide to start on the next page load. Used when the player asks
 *  for it while a different puzzle is open: switching levels is a navigation,
 *  so the intent has to survive one. */
export function armAutostart(): void {
  try {
    localStorage.setItem(AUTOSTART_KEY, "1");
  } catch {
    // Without storage the guide simply does not auto-open after the
    // navigation; the toolbar button still starts it by hand.
  }
}

function consumeAutostart(): boolean {
  try {
    const armed = localStorage.getItem(AUTOSTART_KEY) === "1";
    if (armed) localStorage.removeItem(AUTOSTART_KEY);
    return armed;
  } catch {
    return false;
  }
}

/** {{net}} -> <code>net</code>, and nothing else: step text is authored, but
 *  routing it through innerHTML anyway would be a habit worth not having. */
function renderBody(text: string): HTMLElement {
  const p = document.createElement("p");
  for (const part of text.split(/(\{\{.*?\}\})/g)) {
    if (part.startsWith("{{") && part.endsWith("}}")) {
      const code = document.createElement("code");
      code.textContent = part.slice(2, -2);
      p.append(code);
    } else if (part) {
      p.append(document.createTextNode(part));
    }
  }
  return p;
}

export class Guide {
  private state = loadState();
  private readonly root: HTMLElement;
  private timer = 0;
  private onChange: (() => void) | null = null;

  constructor(private readonly workspace: Workspace) {
    this.root = document.createElement("div");
    this.root.className = "guide-card";
    this.root.hidden = true;
    document.body.append(this.root);

    if (consumeAutostart()) {
      this.state.active = true;
      this.state.collapsed = false;
    }
    if (this.state.active) this.open(this.state.index);
    else this.render();
  }

  get active(): boolean {
    return this.state.active;
  }

  /** Fired when the guide opens or closes, so the toolbar button can retitle
   *  itself. */
  subscribe(fn: () => void): void {
    this.onChange = fn;
  }

  /** Start (or resume) the walkthrough. `restart` sends a returning player
   *  back to step one rather than where they stopped. */
  open(index = this.state.index, restart = false): void {
    this.state.active = true;
    this.state.index = restart ? 0 : Math.min(Math.max(index, 0), STEPS.length - 1);
    this.state.collapsed = false;
    this.save();
    this.focusPanel();
    this.render();
    this.startPolling();
    this.onChange?.();
  }

  close(): void {
    this.state.active = false;
    this.save();
    this.stopPolling();
    this.render();
    this.onChange?.();
  }

  private go(delta: number): void {
    this.state.index = Math.min(Math.max(this.state.index + delta, 0), STEPS.length - 1);
    this.save();
    this.focusPanel();
    this.render();
  }

  private jump(index: number): void {
    this.state.index = index;
    this.save();
    this.focusPanel();
    this.render();
  }

  private get step(): GuideStep {
    return STEPS[this.state.index];
  }

  private satisfied(step: GuideStep): boolean {
    return this.state.satisfied.includes(step.id);
  }

  /** Runs the current step's check and latches the result. Only the current
   *  step is polled: the others' panels are not on screen, so their checks
   *  would be reading a detached DOM. */
  private poll(): void {
    const step = this.step;
    if (!step.done || this.satisfied(step)) return;
    let ok = false;
    try {
      ok = step.done();
    } catch {
      // A check that throws is a check that has gone stale against a panel
      // it no longer matches. That must never take the walkthrough down
      // with it -- the tick just never lights.
      ok = false;
    }
    if (!ok) return;
    this.state.satisfied = [...this.state.satisfied, step.id];
    this.save();
    this.render();
  }

  private startPolling(): void {
    this.stopPolling();
    this.timer = window.setInterval(() => this.poll(), POLL_MS);
  }

  private stopPolling(): void {
    if (this.timer) window.clearInterval(this.timer);
    this.timer = 0;
  }

  private focusPanel(): void {
    const id = this.step.panel;
    if (id) this.workspace.focus(id);
  }

  private save(): void {
    try {
      localStorage.setItem(STATE_KEY, JSON.stringify(this.state));
    } catch {
      // The walkthrough still works; it just restarts from step one next
      // time. Not worth interrupting anyone over.
    }
  }

  private render(): void {
    this.root.hidden = !this.state.active;
    this.root.classList.toggle("guide-collapsed", this.state.collapsed);
    if (!this.state.active) {
      this.root.replaceChildren();
      return;
    }

    const step = this.step;
    const n = this.state.index;
    const frag = document.createDocumentFragment();

    const head = document.createElement("div");
    head.className = "guide-head";
    const badge = document.createElement("span");
    badge.className = "guide-badge";
    badge.textContent = "GUIDE";
    const count = document.createElement("span");
    count.className = "guide-count";
    count.textContent = `${n + 1} / ${STEPS.length}`;

    const collapseBtn = document.createElement("button");
    collapseBtn.type = "button";
    collapseBtn.className = "guide-collapse";
    collapseBtn.title = this.state.collapsed ? "expand" : "collapse";
    collapseBtn.textContent = this.state.collapsed ? "▲" : "▼";
    collapseBtn.addEventListener("click", () => {
      this.state.collapsed = !this.state.collapsed;
      this.save();
      this.render();
    });

    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "guide-close";
    closeBtn.title = "close the walkthrough (reopen it from the toolbar)";
    closeBtn.textContent = "✕";
    closeBtn.addEventListener("click", () => this.close());

    head.append(badge, count, collapseBtn, closeBtn);
    frag.append(head);

    if (!this.state.collapsed) {
      const title = document.createElement("h2");
      title.className = "guide-title";
      title.textContent = step.title;
      frag.append(title);

      const body = document.createElement("div");
      body.className = "guide-body";
      for (const text of step.body) body.append(renderBody(text));
      frag.append(body);

      if (step.goal) {
        const goal = document.createElement("div");
        const ok = this.satisfied(step);
        goal.className = ok ? "guide-goal guide-goal-done" : "guide-goal";
        goal.textContent = `${ok ? "✓" : "○"}  ${step.goal}`;
        frag.append(goal);
      }
    }

    const foot = document.createElement("div");
    foot.className = "guide-foot";

    const back = document.createElement("button");
    back.type = "button";
    back.textContent = "◀ back";
    back.disabled = n === 0;
    back.addEventListener("click", () => this.go(-1));

    const next = document.createElement("button");
    next.type = "button";
    next.className = "guide-next";
    next.textContent = n === STEPS.length - 1 ? "finish" : "next ▶";
    next.addEventListener("click", () => {
      if (n === STEPS.length - 1) this.close();
      else this.go(1);
    });

    const jumpSel = document.createElement("select");
    jumpSel.className = "guide-jump";
    jumpSel.title = "jump to any step";
    STEPS.forEach((s, i) => {
      const opt = document.createElement("option");
      opt.value = String(i);
      opt.textContent = `${i + 1}. ${s.title}`;
      jumpSel.append(opt);
    });
    jumpSel.value = String(n);
    jumpSel.addEventListener("change", () => this.jump(Number(jumpSel.value)));

    foot.append(back, next, jumpSel);
    frag.append(foot);

    this.root.replaceChildren(frag);
  }
}