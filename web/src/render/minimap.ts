// A Canvas2D overview of the whole die: instance centres as a faint
// scatter, plus the main Die View's current viewport as a draggable
// rectangle. Deliberately not a second WebGL scene -- an orientation aid
// doesn't need instanced draws, and Canvas2D redraws this cheaply enough to
// run every frame off the same rAF loop as the die view.

import type { RenderBundle } from "./bundle";
import type { DieView } from "./dieview";

export class Minimap {
  private readonly ctx: CanvasRenderingContext2D;
  private readonly bbox: [number, number, number, number];
  /** Decimated instance centres, x,y pairs, database units. */
  private readonly points: Float32Array;

  constructor(
    private readonly canvas: HTMLCanvasElement,
    bundle: RenderBundle,
    private readonly die: DieView,
  ) {
    this.ctx = canvas.getContext("2d")!;
    this.bbox = bundle.header.bbox;

    const inst = bundle.view(bundle.header.instances); // stride 6: x0,y0,x1,y1,orient,cell_id
    const n = bundle.header.instances.count;
    const cap = 4000; // looks solid at minimap scale; redrawing more buys nothing
    const step = Math.max(1, Math.floor(n / cap));
    const pts: number[] = [];
    for (let i = 0; i < n; i += step) {
      const o = i * 6;
      pts.push((inst[o] + inst[o + 2]) / 2, (inst[o + 1] + inst[o + 3]) / 2);
    }
    this.points = new Float32Array(pts);

    this.attachInput();
  }

  private scale(): { s: number; ox: number; oy: number } {
    const [x0, y0, x1, y1] = this.bbox;
    const w = this.canvas.width || 1;
    const h = this.canvas.height || 1;
    const s = Math.min(w / Math.max(x1 - x0, 1), h / Math.max(y1 - y0, 1));
    return {
      s,
      ox: (w - (x1 - x0) * s) / 2,
      oy: (h - (y1 - y0) * s) / 2,
    };
  }

  private toCanvas(x: number, y: number): [number, number] {
    const [x0, y0] = this.bbox;
    const { s, ox, oy } = this.scale();
    return [ox + (x - x0) * s, this.canvas.height - (oy + (y - y0) * s)];
  }

  private toWorld(cx: number, cy: number): [number, number] {
    const [x0, y0] = this.bbox;
    const { s, ox, oy } = this.scale();
    return [x0 + (cx - ox) / s, y0 + (this.canvas.height - cy - oy) / s];
  }

  private attachInput(): void {
    const jump = (e: PointerEvent) => {
      const rect = this.canvas.getBoundingClientRect();
      const sx = (e.clientX - rect.left) * (this.canvas.width / rect.width);
      const sy = (e.clientY - rect.top) * (this.canvas.height / rect.height);
      const [wx, wy] = this.toWorld(sx, sy);
      this.die.view.centre = [wx, wy];
    };
    let dragging = false;
    this.canvas.addEventListener("pointerdown", (e) => {
      dragging = true;
      this.canvas.setPointerCapture(e.pointerId);
      jump(e);
    });
    this.canvas.addEventListener("pointermove", (e) => {
      if (dragging) jump(e);
    });
    this.canvas.addEventListener("pointerup", (e) => {
      dragging = false;
      this.canvas.releasePointerCapture(e.pointerId);
    });
  }

  /** Call once per frame from the same loop that drives the die view. */
  render(): void {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.round(this.canvas.clientWidth * dpr);
    const h = Math.round(this.canvas.clientHeight * dpr);
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w;
      this.canvas.height = h;
    }
    const ctx = this.ctx;
    ctx.fillStyle = "#0d0f14";
    ctx.fillRect(0, 0, w, h);

    ctx.fillStyle = "rgba(140, 148, 168, 0.6)";
    for (let i = 0; i < this.points.length; i += 2) {
      const [cx, cy] = this.toCanvas(this.points[i], this.points[i + 1]);
      ctx.fillRect(cx, cy, 1, 1);
    }

    const [x0, y0, x1, y1] = this.die.visibleWorldRect();
    const [cx0, cy0] = this.toCanvas(x0, y1);
    const [cx1, cy1] = this.toCanvas(x1, y0);
    ctx.strokeStyle = "#f2cc4d";
    ctx.lineWidth = Math.max(1, dpr);
    ctx.strokeRect(cx0, cy0, cx1 - cx0, cy1 - cy0);
  }
}