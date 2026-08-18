// Minimal WebGL2 die view: pan, zoom, layer toggles. No selection, no
// highlighting, no panels -- this is the M0 spike half that proves the
// render bundle can be drawn at interactive rates.
//
// Every layer is one instanced draw of a unit quad, with the per-instance
// rectangle read straight out of the bundle's Int32Array as integer database
// units (`vertexAttribIPointer`). Nothing is converted to microns or to
// floats on the CPU; the vertex shader does the one multiply.
//
// Tiling: rects within a layer are sorted by tile index and the header
// carries a prefix-offset array, so a frame draws only the tile rows the
// viewport actually covers -- at most `rows` draw calls per layer instead of
// one over the whole die.

import type { RenderBundle } from "./bundle";

const VERT = `#version 300 es
in vec2 a_corner;          // unit quad, 0..1
in ivec4 a_rect;           // x0,y0,x1,y1 in database units
uniform vec2 u_center;     // view centre, database units
uniform vec2 u_scale;      // clip units per database unit
uniform vec2 u_minSize;    // database units per pixel
void main() {
  vec2 lo = vec2(a_rect.xy);
  vec2 hi = vec2(a_rect.zw);
  // A sub-pixel rect would vanish; widen it to about a pixel so thin wires
  // stay visible when the whole die is on screen. u_minSize is one pixel in
  // database units -- NOT 1/u_scale, which is the whole viewport and turns
  // every wire into a die-sized quad. That mistake cost 90 ms a frame.
  hi = max(hi, lo + u_minSize);
  vec2 world = mix(lo, hi, a_corner);
  gl_Position = vec4((world - u_center) * u_scale, 0.0, 1.0);
}`;

const FRAG = `#version 300 es
precision highp float;
uniform vec4 u_colour;
out vec4 fragColour;
void main() { fragColour = u_colour; }`;

/** sky130-ish layer colours, in the KLayout spirit: li1 up through met5. */
const COLOURS: Record<string, [number, number, number, number]> = {
  li1: [0.42, 0.75, 0.42, 0.75],
  met1: [0.35, 0.55, 0.95, 0.7],
  met2: [0.95, 0.45, 0.35, 0.65],
  met3: [0.95, 0.8, 0.3, 0.6],
  met4: [0.75, 0.4, 0.9, 0.6],
  met5: [0.4, 0.9, 0.9, 0.6],
};
const INSTANCE_COLOUR: [number, number, number, number] = [0.55, 0.58, 0.66, 0.5];

function compile(gl: WebGL2RenderingContext, type: number, src: string): WebGLShader {
  const sh = gl.createShader(type)!;
  gl.shaderSource(sh, src);
  gl.compileShader(sh);
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
    throw new Error(`shader: ${gl.getShaderInfoLog(sh)}`);
  }
  return sh;
}

interface Batch {
  name: string;
  vao: WebGLVertexArrayObject;
  colour: [number, number, number, number];
  /** Prefix offsets into the instance buffer, one per tile + 1. Null = untiled. */
  tiles: Int32Array | null;
  count: number;
  /** Kept so a tiled draw can re-point the attribute at a mid-buffer run. */
  buffer: WebGLBuffer;
  strideBytes: number;
  offsetBytes: number;
}

export interface ViewState {
  centre: [number, number];
  /** Database units per CSS pixel. Larger = further out. */
  unitsPerPixel: number;
}

export class DieView {
  private readonly gl: WebGL2RenderingContext;
  private readonly program: WebGLProgram;
  private readonly uCentre: WebGLUniformLocation;
  private readonly uScale: WebGLUniformLocation;
  private readonly uColour: WebGLUniformLocation;
  private readonly uMinSize: WebGLUniformLocation;
  /** batches[lod][layerName], plus a shared instance batch for LOD2. */
  private readonly batches: Map<string, Batch>[] = [];
  private instanceBatch!: Batch;
  private readonly enabled = new Set<string>();
  private readonly cols: number;
  private readonly rows: number;
  private readonly bbox: [number, number, number, number];

  view: ViewState;
  /** 0, 1, 2, or "auto" -- pick by zoom. */
  lodMode: number | "auto" = "auto";
  /** Last frame's draw call and instance counts, for the perf report. */
  lastFrame = { drawCalls: 0, instances: 0, lod: 0 };

  constructor(
    private readonly canvas: HTMLCanvasElement,
    bundle: RenderBundle,
  ) {
    const gl = canvas.getContext("webgl2", {
      antialias: false,
      alpha: false,
      powerPreference: "high-performance",
    });
    if (!gl) throw new Error("WebGL2 is not available in this browser");
    this.gl = gl;

    this.program = gl.createProgram()!;
    gl.attachShader(this.program, compile(gl, gl.VERTEX_SHADER, VERT));
    gl.attachShader(this.program, compile(gl, gl.FRAGMENT_SHADER, FRAG));
    gl.linkProgram(this.program);
    if (!gl.getProgramParameter(this.program, gl.LINK_STATUS)) {
      throw new Error(`link: ${gl.getProgramInfoLog(this.program)}`);
    }
    this.uCentre = gl.getUniformLocation(this.program, "u_center")!;
    this.uScale = gl.getUniformLocation(this.program, "u_scale")!;
    this.uColour = gl.getUniformLocation(this.program, "u_colour")!;
    this.uMinSize = gl.getUniformLocation(this.program, "u_minSize")!;

    const h = bundle.header;
    this.bbox = h.bbox;
    this.cols = h.tile_grid.cols;
    this.rows = h.tile_grid.rows;

    const quad = gl.createBuffer()!;
    gl.bindBuffer(gl.ARRAY_BUFFER, quad);
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([0, 0, 1, 0, 0, 1, 1, 1]),
      gl.STATIC_DRAW,
    );

    for (let lod = 0; lod < h.lod_levels; lod++) {
      const perLayer = new Map<string, Batch>();
      const layers = h.lods[String(lod)] ?? {};
      for (const name of h.layers) {
        const slice = layers[name];
        if (!slice || slice.rects.count === 0) continue;
        perLayer.set(
          name,
          this.makeBatch(
            name,
            quad,
            bundle.view(slice.rects),
            16,
            0,
            COLOURS[name] ?? [1, 1, 1, 0.5],
            bundle.view(slice.tiles),
            slice.rects.count,
          ),
        );
      }
      this.batches.push(perLayer);
    }

    // Instances are LOD-independent: [x0,y0,x1,y1,orient,cell_id] * n.
    this.instanceBatch = this.makeBatch(
      "instances",
      quad,
      bundle.view(h.instances),
      24,
      0,
      INSTANCE_COLOUR,
      null,
      h.instances.count,
    );

    for (const name of h.layers) this.enabled.add(name);
    this.enabled.add("instances");

    const [x0, y0, x1, y1] = this.bbox;
    this.view = { centre: [(x0 + x1) / 2, (y0 + y1) / 2], unitsPerPixel: 1 };
    this.fit();
    this.attachInput();

    gl.clearColor(0.05, 0.06, 0.08, 1);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
  }

  private makeBatch(
    name: string,
    quad: WebGLBuffer,
    data: Int32Array,
    strideBytes: number,
    offsetBytes: number,
    colour: [number, number, number, number],
    tiles: Int32Array | null,
    count: number,
  ): Batch {
    const gl = this.gl;
    const vao = gl.createVertexArray()!;
    gl.bindVertexArray(vao);

    gl.bindBuffer(gl.ARRAY_BUFFER, quad);
    const corner = gl.getAttribLocation(this.program, "a_corner");
    gl.enableVertexAttribArray(corner);
    gl.vertexAttribPointer(corner, 2, gl.FLOAT, false, 0, 0);

    const buf = gl.createBuffer()!;
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
    const rect = gl.getAttribLocation(this.program, "a_rect");
    gl.enableVertexAttribArray(rect);
    gl.vertexAttribIPointer(rect, 4, gl.INT, strideBytes, offsetBytes);
    gl.vertexAttribDivisor(rect, 1);

    gl.bindVertexArray(null);
    return { name, vao, colour, tiles, count, buffer: buf, strideBytes, offsetBytes };
  }

  /** Attribute location of `a_rect`, needed to re-point a tiled draw. */
  private rectAttrib(): number {
    return this.gl.getAttribLocation(this.program, "a_rect");
  }

  // ---- camera -------------------------------------------------------------

  /** Frame the whole die. */
  fit(): void {
    const [x0, y0, x1, y1] = this.bbox;
    const w = this.canvas.clientWidth || 1;
    const h = this.canvas.clientHeight || 1;
    this.view = {
      centre: [(x0 + x1) / 2, (y0 + y1) / 2],
      unitsPerPixel: Math.max((x1 - x0) / w, (y1 - y0) / h) * 1.05,
    };
  }

  /** How many database units of die are on screen, as a fraction of the die. */
  private zoomFraction(): number {
    const [x0, , x1] = this.bbox;
    return (this.view.unitsPerPixel * (this.canvas.clientWidth || 1)) / (x1 - x0);
  }

  private activeLod(): number {
    if (this.lodMode !== "auto") return this.lodMode;
    const f = this.zoomFraction();
    // Measured on an M4 Pro: the whole die at LOD0 is 76k rects in 80 draws
    // and still holds 60 fps at 64x that load, so dropping the routing at
    // full-die zoom would be throwing away the view the game is built around
    // for no gain. Auto therefore never picks LOD2 -- it stays reachable from
    // the dropdown, and is the fallback if a weaker GPU ever needs it.
    return f > 0.75 ? 1 : 0;
  }

  private attachInput(): void {
    const c = this.canvas;
    let dragging = false;
    let lastX = 0;
    let lastY = 0;

    c.addEventListener("pointerdown", (e) => {
      dragging = true;
      lastX = e.clientX;
      lastY = e.clientY;
      c.setPointerCapture(e.pointerId);
    });
    c.addEventListener("pointerup", (e) => {
      dragging = false;
      c.releasePointerCapture(e.pointerId);
    });
    c.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      const k = this.view.unitsPerPixel;
      this.view.centre[0] -= (e.clientX - lastX) * k;
      this.view.centre[1] += (e.clientY - lastY) * k;
      lastX = e.clientX;
      lastY = e.clientY;
    });
    c.addEventListener(
      "wheel",
      (e) => {
        e.preventDefault();
        const rect = c.getBoundingClientRect();
        // Zoom about the cursor: the die point under it must not move.
        const px = e.clientX - rect.left - rect.width / 2;
        const py = rect.height / 2 - (e.clientY - rect.top);
        const before = this.view.unitsPerPixel;
        const after = before * Math.exp(e.deltaY * 0.002);
        this.view.unitsPerPixel = Math.min(Math.max(after, 0.05), 4000);
        const d = this.view.unitsPerPixel - before;
        this.view.centre[0] -= px * d;
        this.view.centre[1] -= py * d;
      },
      { passive: false },
    );
  }

  // ---- layers -------------------------------------------------------------

  setLayer(name: string, on: boolean): void {
    if (on) this.enabled.add(name);
    else this.enabled.delete(name);
  }

  isLayerOn(name: string): boolean {
    return this.enabled.has(name);
  }

  // ---- drawing ------------------------------------------------------------

  /** Visible tile columns/rows, clamped to the grid. */
  private visibleTiles(): { c0: number; c1: number; r0: number; r1: number } {
    const [bx0, by0, bx1, by1] = this.bbox;
    const w = Math.max(bx1 - bx0, 1);
    const h = Math.max(by1 - by0, 1);
    const halfW = (this.canvas.clientWidth / 2) * this.view.unitsPerPixel;
    const halfH = (this.canvas.clientHeight / 2) * this.view.unitsPerPixel;
    const [cx, cy] = this.view.centre;
    const c0 = Math.floor(((cx - halfW - bx0) * this.cols) / w);
    const c1 = Math.floor(((cx + halfW - bx0) * this.cols) / w);
    const r0 = Math.floor(((cy - halfH - by0) * this.rows) / h);
    const r1 = Math.floor(((cy + halfH - by0) * this.rows) / h);
    return {
      c0: Math.max(0, c0),
      c1: Math.min(this.cols - 1, c1),
      r0: Math.max(0, r0),
      r1: Math.min(this.rows - 1, r1),
    };
  }

  private drawBatch(b: Batch): void {
    const gl = this.gl;
    gl.bindVertexArray(b.vao);
    gl.uniform4fv(this.uColour, b.colour);

    if (!b.tiles) {
      gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, b.count);
      this.lastFrame.drawCalls++;
      this.lastFrame.instances += b.count;
      return;
    }

    // Tiles are row-major, so one visible row is one contiguous run.
    const { c0, c1, r0, r1 } = this.visibleTiles();
    if (c1 < c0 || r1 < r0) return;
    const loc = this.rectAttrib();
    gl.bindBuffer(gl.ARRAY_BUFFER, b.buffer);
    for (let r = r0; r <= r1; r++) {
      const start = b.tiles[r * this.cols + c0];
      const end = b.tiles[r * this.cols + c1 + 1];
      const n = end - start;
      if (n <= 0) continue;
      // WebGL2 has no baseInstance, so shift the attribute pointer at the
      // run's first instance rather than uploading a sub-range per frame.
      gl.vertexAttribIPointer(
        loc,
        4,
        gl.INT,
        b.strideBytes,
        b.offsetBytes + start * b.strideBytes,
      );
      gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, n);
      this.lastFrame.drawCalls++;
      this.lastFrame.instances += n;
    }
    gl.vertexAttribIPointer(loc, 4, gl.INT, b.strideBytes, b.offsetBytes);
  }

  render(): void {
    const gl = this.gl;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.round(this.canvas.clientWidth * dpr);
    const h = Math.round(this.canvas.clientHeight * dpr);
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w;
      this.canvas.height = h;
    }
    gl.viewport(0, 0, w, h);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.useProgram(this.program);

    const k = this.view.unitsPerPixel;
    gl.uniform2f(this.uCentre, this.view.centre[0], this.view.centre[1]);
    gl.uniform2f(
      this.uScale,
      2 / (this.canvas.clientWidth * k),
      2 / (this.canvas.clientHeight * k),
    );
    gl.uniform2f(this.uMinSize, k, k);

    this.lastFrame = { drawCalls: 0, instances: 0, lod: this.activeLod() };

    // Cells underneath, routing over them, in stack order.
    if (this.enabled.has("instances")) this.drawBatch(this.instanceBatch);
    const lod = this.activeLod();
    for (const [name, batch] of this.batches[lod] ?? []) {
      if (this.enabled.has(name)) this.drawBatch(batch);
    }
    gl.bindVertexArray(null);
  }

  /** The GL renderer string, for an honest note on what drew the frames. */
  rendererInfo(): string {
    const gl = this.gl;
    const ext = gl.getExtension("WEBGL_debug_renderer_info");
    if (!ext) return gl.getParameter(gl.RENDERER) as string;
    return `${gl.getParameter(ext.UNMASKED_VENDOR_WEBGL)} / ${gl.getParameter(
      ext.UNMASKED_RENDERER_WEBGL,
    )}`;
  }
}