// three.js Die View 3D: the same render bundle as `dieview.ts`, extruded
// through the real sky130 z-stack (`header.stack`) -- the 3D
// sibling of the 2D die view.
//
// One `THREE.InstancedMesh` per routing layer, LOD1 only (the perf budget's
// hard cap -- 30 fps min, LOD1 cap, instanced boxes). Each
// layer's rects are flattened out of their tile buckets into one flat
// instance buffer; three.js instancing does not need the 2D view's
// per-tile-draw-call trick, since a whole layer is one `drawArraysInstanced`
// either way.
//
// Net highlighting reuses the same net-id-per-shape scheme `dieview.ts`
// builds (`header.net_of_shape` cross-referenced with each layer's LOD1
// `shape_ids`): a dense net id is computed once per instance at load and
// kept alongside the mesh, the same data `netIdsFor`/`cpuNetIds` compute
// there. Net tracing walks that same per-layer array rather than
// re-deriving `header.net_shapes.offsets/ids` at trace time -- it is the
// same cross-reference, already materialised, so there is no reason to
// redo it per trace.
//
// Explode and cross-section are both "free": explode only ever moves a
// layer's `InstancedMesh.position.z` (one float, not a buffer), and the
// section plane is one shared `THREE.Plane` whose `constant` is rewritten on
// slider input. Only layer visibility and net-highlight colour touch the
// instance buffers themselves, and only on change -- never per frame.

import * as THREE from "three";
import type { RenderBundle } from "./bundle";

/** sky130-ish layer colours -- kept in step with `dieview.ts`'s `COLOURS`
 * (duplicated rather than imported: the 2D view's table is an internal
 * shader constant, not a shared export, and the two views are allowed to
 * drift slightly, e.g. per-layer opacity reads differently as a WebGL
 * blend vs. a three.js material). */
const LAYER_COLOUR: Record<string, { hex: number; opacity: number }> = {
  // Device level, in the KLayout/sky130 spirit: earthy and near-opaque, so it
  // reads as the solid thing the metal sits on rather than as more wiring.
  substrate: { hex: 0x3a3f4a, opacity: 1.0 },
  nwell: { hex: 0x6b5b8c, opacity: 0.95 },
  diff: { hex: 0x8c6b4a, opacity: 0.95 },
  tap: { hex: 0xa88a5c, opacity: 0.95 },
  poly: { hex: 0xd94f70, opacity: 0.92 },
  licon1: { hex: 0xbfbfbf, opacity: 0.9 },
  // Interconnect: progressively more transparent going up, so the lower
  // layers stay visible through the ones above.
  li1: { hex: 0x6bbf6b, opacity: 0.85 },
  met1: { hex: 0x598cf2, opacity: 0.8 },
  met2: { hex: 0xf2735a, opacity: 0.78 },
  met3: { hex: 0xf2cc4d, opacity: 0.75 },
  met4: { hex: 0xbf66e6, opacity: 0.72 },
  met5: { hex: 0x66e6e6, opacity: 0.7 },
};
const HIGHLIGHT_COLOUR = 0xffd926; // matches dieview.ts's (1, 0.85, 0.15)
const DIM_FACTOR = 0.35; // matches dieview.ts's non-selected rgb *= 0.35
const PULSE_COLOUR = 0xffffff;

/**
 * The floor of the next *drawn* layer above `index`, or null if this is the
 * top one.
 *
 * Walks `layerOrder` rather than `header.stack`, because the stack also lists
 * the via tiers and those are never drawn -- the answer wanted here is "where
 * does the next visible slab start", which is what a layer has to reach to
 * look joined to it.
 */
function drawnStackTop(
  drawnOrder: readonly string[],
  index: number,
  stackByName: Map<string, { z: number; thickness: number }>,
): number | null {
  for (let i = index + 1; i < drawnOrder.length; i++) {
    const next = stackByName.get(drawnOrder[i]);
    if (next) return next.z;
  }
  return null;
}

/**
 * How much to compress the z axis, given the die's lateral size.
 *
 * The stack is ~7 um tall whatever the die is. On the 200 um reference die
 * that is 3 % of the width and reads as a chip; on a 26 um puzzle it is a
 * quarter of the width and reads as a layer cake floating in space. Nothing
 * is wrong with the geometry -- the small dies really are that proportion --
 * but the point of the view is to look like a chip, so the z axis is squashed
 * until the stack occupies about the same fraction of the frame it does on
 * the reference die.
 *
 * Never *stretches*: a die big enough already is left at true scale, so the
 * reference puzzle is unchanged and the compression only ever pulls the small
 * dies back towards it.
 */
function zCompression(dieWidthUm: number, dieHeightUm: number, stackUm: number): number {
  if (stackUm <= 0) return 1;
  const lateral = Math.max(dieWidthUm, dieHeightUm);
  return Math.min(1, lateral / (STACK_TO_DIE_RATIO * stackUm));
}

/** An angle folded into (-PI, PI]. */
function wrapAngle(radians: number): number {
  const twoPi = Math.PI * 2;
  const wrapped = radians % twoPi;
  if (wrapped > Math.PI) return wrapped - twoPi;
  if (wrapped <= -Math.PI) return wrapped + twoPi;
  return wrapped;
}

/** Rects below this, in microns, would render as a sliver or vanish. */
const MIN_SIZE_UM = 0.03;
/** How far apart the exploded view pulls adjacent stack layers, at factor 1.
 *  Scaled by the same z compression as the stack itself, so exploding a
 *  squashed die does not jump. */
const EXPLODE_SEPARATION_UM = 3.0;
/** The die's larger lateral dimension, in stack heights, that `zCompression`
 *  aims for. Measured from the reference die: 200 um across a 6.6 um stack is
 *  ~30, and that is the proportion the view is trying to reproduce. */
const STACK_TO_DIE_RATIO = 26;
/** How long the trace sweep dwells on each layer. */
const TRACE_STEP_MS = 450;

interface LayerMesh {
  name: string;
  mesh: THREE.InstancedMesh;
  material: THREE.MeshBasicMaterial;
  /** Dense net id per instance, same order as the instance buffer. -1 = none. */
  netIds: Int32Array;
  baseColour: THREE.Color;
  dimColour: THREE.Color;
  /** This layer's position among `header.layers`, bottom (li1) to top -- the
   *  explode multiplier and the trace sweep's visit order both key off it. */
  index: number;
}

export interface NetHit3D {
  id: number;
  name: string;
}

function webgl2Available(): boolean {
  try {
    const canvas = document.createElement("canvas");
    return !!canvas.getContext("webgl2");
  } catch {
    return false;
  }
}

export class Die3D {
  private readonly renderer: THREE.WebGLRenderer;
  private readonly scene = new THREE.Scene();
  private readonly camera: THREE.PerspectiveCamera;
  private readonly layers = new Map<string, LayerMesh>();
  private geometry: THREE.BoxGeometry | null = null;
  private readonly layerOrder: string[];
  private readonly enabled = new Set<string>();
  private readonly netNameById = new Map<number, string>();
  private readonly netIdByName = new Map<string, number>();
  private readonly plane = new THREE.Plane(new THREE.Vector3(-1, 0, 0), 0);
  private readonly dieWidthUm: number;
  private readonly dieHeightUm: number;
  /** Height of the drawn stack *after* z compression, in scene units. */
  private readonly stackHeightUm: number;
  /** Bottom of the drawn stack after compression -- the substrate sits below
   *  z = 0, so this is negative and the camera has to know about it. */
  private readonly stackFloorUm: number;
  /** See `zCompression`. 1.0 on a die large enough not to need squashing. */
  private readonly zScale: number;

  /** Mid-height of the drawn stack -- what the camera orbits about. */
  private get stackCentreUm(): number {
    return this.stackFloorUm + this.stackHeightUm / 2;
  }
  private readonly raycaster = new THREE.Raycaster();

  private highlightedNet = -1;
  private explodeFactor = 0;
  private sectionEnabled = false;
  private sectionT = 0.5;
  private traceState: { netId: number; order: string[]; startTime: number } | null = null;
  private traceActiveLayer: string | null = null;
  private traceLabel: string | null = null;

  // Orbit camera: spherical around a target that starts at the stack's
  // midpoint and can be panned across the die.
  private azimuth = Math.PI / 4;
  private elevation = 0.6;
  private radius: number;
  private readonly target: THREE.Vector3;

  /** Latest cursor position awaiting a hover raycast, consumed in `render`. */
  private hoverPoint: { x: number; y: number } | null = null;
  private hoveredNet = -1;

  constructor(
    private readonly canvas: HTMLCanvasElement,
    bundle: RenderBundle,
  ) {
    if (!webgl2Available()) throw new Error("WebGL2 is not available in this browser");

    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
    this.renderer.localClippingEnabled = true;
    this.renderer.setClearColor(0x0d0f14, 1);

    const h = bundle.header;
    this.layerOrder = h.layers;
    for (const [id, name] of Object.entries(h.net_names)) {
      const n = Number(id);
      this.netNameById.set(n, name);
      this.netIdByName.set(name, n);
    }

    const [bx0, by0, bx1, by1] = h.bbox;
    this.dieWidthUm = (bx1 - bx0) * h.dbu;
    this.dieHeightUm = (by1 - by0) * h.dbu;
    const dieCx = ((bx0 + bx1) / 2) * h.dbu;
    const dieCy = ((by0 + by1) / 2) * h.dbu;

    const stackByName = new Map(h.stack.map((s) => [s.name, s]));
    const stackFloor = h.stack.reduce((m, s) => Math.min(m, s.z), 0);
    const stackTop = h.stack.reduce((m, s) => Math.max(m, s.z + s.thickness), 0);

    const netOfShape = bundle.view(h.net_of_shape);
    const lod1 = h.lods["1"] ?? {};

    // Only the layers that actually got geometry take part in the gap-filling
    // below. A layer listed in the header but empty in this design -- a
    // contact tier dropped at this LOD, or a metal the design never used --
    // must not be treated as the ceiling of the layer beneath it, or that
    // layer stops short and leaves exactly the floating gap the fill exists
    // to close.
    const drawnOrder = this.layerOrder.filter(
      (name) => (lod1[name]?.rects.count ?? 0) > 0 && stackByName.has(name),
    );

    this.zScale = zCompression(this.dieWidthUm, this.dieHeightUm, stackTop - stackFloor);
    this.stackHeightUm = (stackTop - stackFloor) * this.zScale;
    this.stackFloorUm = stackFloor * this.zScale;

    // One box geometry, shared by every layer's InstancedMesh -- only the
    // per-instance matrix (position/scale) and colour differ.
    const boxGeometry = new THREE.BoxGeometry(1, 1, 1);
    // `vertexColors: true` (below) makes every layer's shader multiply by a
    // per-vertex `color` attribute before applying the per-instance colour --
    // that is how three.js's USE_COLOR chunk is written, regardless of
    // whether the multiply is wanted. `BoxGeometry` has no such attribute, so
    // without this the shader reads an unset attribute location (0,0,0) and
    // every instance renders black no matter what colour it was given. A flat
    // white per-vertex colour makes that multiply a no-op and leaves the
    // per-instance colour as the only thing that actually paints the box.
    boxGeometry.setAttribute(
      "color",
      new THREE.BufferAttribute(new Float32Array(boxGeometry.attributes.position.count * 3).fill(1), 3),
    );
    this.geometry = boxGeometry;

    for (let index = 0; index < this.layerOrder.length; index++) {
      const name = this.layerOrder[index];
      const slice = lod1[name];
      const stackEntry = stackByName.get(name);
      if (!slice || slice.rects.count === 0 || !stackEntry) continue;

      const rects = bundle.view(slice.rects);
      const shapeIds = bundle.view(slice.shape_ids);
      const count = slice.rects.count;
      const netIds = new Int32Array(count);

      const colourDef = LAYER_COLOUR[name] ?? { hex: 0xffffff, opacity: 0.6 };
      const material = new THREE.MeshBasicMaterial({
        color: 0xffffff,
        vertexColors: true,
        transparent: true,
        opacity: colourDef.opacity,
        side: THREE.FrontSide,
      });
      const mesh = new THREE.InstancedMesh(boxGeometry, material, count);

      const m = new THREE.Matrix4();
      const pos = new THREE.Vector3();
      const scale = new THREE.Vector3();
      const quat = new THREE.Quaternion();
      // Drawn from this layer's floor up to the next drawn layer's floor,
      // rather than to its own true ceiling.
      //
      // `header.stack` is contiguous -- every metal's top is the bottom of
      // the via tier above it, and that via tier's top is the bottom of the
      // next metal. But `header.layers` lists only the routing layers, so no
      // geometry is ever built for the vias, and drawing each metal at its
      // true thickness left a floating stack of slabs with the exact height
      // of the missing via tier between each pair -- 0.27 to 0.5 um of
      // nothing, which at any sensible zoom reads as a design that is not
      // connected to itself. Filling the gap makes the stack read as one
      // object, and the extra height is not invented: it is the via tier,
      // drawn as part of the metal below it because there is nothing else
      // to draw it as.
      //
      // `explode` still separates them, so the true layer boundaries are one
      // slider away.
      const nextDrawn = drawnStackTop(drawnOrder, drawnOrder.indexOf(name), stackByName);
      const floor = stackEntry.z;
      const trueSpan =
        nextDrawn === null ? stackEntry.thickness : Math.max(nextDrawn - floor, 0);
      const span = Math.max(trueSpan * this.zScale, MIN_SIZE_UM);
      const cz = floor * this.zScale + span / 2;
      const thickness = span;

      for (let i = 0; i < count; i++) {
        const o = i * 4;
        const x0 = rects[o] * h.dbu;
        const y0 = rects[o + 1] * h.dbu;
        const x1 = rects[o + 2] * h.dbu;
        const y1 = rects[o + 3] * h.dbu;
        const w = Math.max(x1 - x0, MIN_SIZE_UM);
        const d = Math.max(y1 - y0, MIN_SIZE_UM);
        pos.set((x0 + x1) / 2 - dieCx, (y0 + y1) / 2 - dieCy, cz);
        scale.set(w, d, thickness);
        m.compose(pos, quat, scale);
        mesh.setMatrixAt(i, m);

        const netId = netOfShape[shapeIds[i]] ?? -1;
        netIds[i] = netId;
      }
      mesh.instanceMatrix.needsUpdate = true;

      const baseColour = new THREE.Color(colourDef.hex);
      const dimColour = baseColour.clone().multiplyScalar(DIM_FACTOR);
      for (let i = 0; i < count; i++) mesh.setColorAt(i, baseColour);
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;

      this.scene.add(mesh);
      const lm: LayerMesh = { name, mesh, material, netIds, baseColour, dimColour, index };
      this.layers.set(name, lm);
      this.enabled.add(name);
    }

    this.target = new THREE.Vector3(0, 0, this.stackCentreUm);
    this.radius = Math.max(this.dieWidthUm, this.dieHeightUm, this.stackHeightUm) * 1.6 || 10;
    this.camera = new THREE.PerspectiveCamera(45, 1, 0.01, this.radius * 20);
    this.camera.up.set(0, 0, 1);
    this.updateCamera();

    this.attachInput();
  }

  // ---- camera ---------------------------------------------------------

  private updateCamera(): void {
    const ce = Math.cos(this.elevation);
    const se = Math.sin(this.elevation);
    const ca = Math.cos(this.azimuth);
    const sa = Math.sin(this.azimuth);

    this.camera.position.set(
      this.target.x + this.radius * ce * ca,
      this.target.y + this.radius * ce * sa,
      this.target.z + this.radius * se,
    );

    // The up vector is the sphere's own north tangent at this point --
    // d/d(elevation) of the position direction -- rather than a fixed +Z.
    //
    // That is what lets elevation run the whole way round instead of being
    // clamped to the top hemisphere. With a fixed +Z up, `lookAt` degenerates
    // at the poles (the view direction and up become parallel) and flips the
    // image once past them, which is why the old code stopped at 0.05..1.5
    // radians and would not let you look at the die from below. This tangent
    // is continuous everywhere on the sphere, poles included, so there is no
    // orientation the camera cannot reach and none it snaps out of.
    this.camera.up.set(-se * ca, -se * sa, ce);
    this.camera.lookAt(this.target);
  }

  /** Reset to the default 3/4 view of the whole stack. Recentres the target
   *  too, so "Reset view" is a way back from a camera panned off the die --
   *  which is the only way back, since panning has no bounds you can feel. */
  fit(): void {
    this.azimuth = Math.PI / 4;
    this.elevation = 0.6;
    this.radius = Math.max(this.dieWidthUm, this.dieHeightUm, this.stackHeightUm) * 1.6 || 10;
    this.target.set(0, 0, this.stackCentreUm);
    this.updateCamera();
  }

  /**
   * Slide the orbit target across the die, in the camera's own basis, so a
   * drag moves the scene the way the pointer moves however the view is
   * oriented. Scaled by the distance to the target: panning at full-die zoom
   * covers ground, panning up close is fine-grained.
   */
  private pan(dxPixels: number, dyPixels: number): void {
    const height = this.canvas.clientHeight || 1;
    const fov = (this.camera.fov * Math.PI) / 180;
    const unitsPerPixel = (2 * this.radius * Math.tan(fov / 2)) / height;

    this.camera.updateMatrixWorld();
    const m = this.camera.matrixWorld.elements;
    const right = new THREE.Vector3(m[0], m[1], m[2]);
    const up = new THREE.Vector3(m[4], m[5], m[6]);

    this.target.addScaledVector(right, -dxPixels * unitsPerPixel);
    this.target.addScaledVector(up, dyPixels * unitsPerPixel);

    // Bounded to roughly a die's width beyond the die on each side. Without
    // this, one fast drag at full zoom puts the layout off screen with no
    // visible cue about which way to go back.
    const limitX = this.dieWidthUm;
    const limitY = this.dieHeightUm;
    this.target.x = Math.min(limitX, Math.max(-limitX, this.target.x));
    this.target.y = Math.min(limitY, Math.max(-limitY, this.target.y));
    this.target.z = Math.min(
      this.stackFloorUm + this.stackHeightUm * 2,
      Math.max(this.stackFloorUm - this.stackHeightUm, this.target.z),
    );
    this.updateCamera();
  }

  private attachInput(): void {
    const c = this.canvas;
    // Left drag orbits, and any of right / middle / shift-left pans -- the
    // three bindings CAD tools have taught people to reach for, since none of
    // them is available on every mouse and trackpad.
    let mode: "orbit" | "pan" | null = null;
    let dragged = false;
    let button = -1;
    let lastX = 0;
    let lastY = 0;

    c.addEventListener("pointerdown", (e) => {
      mode = e.button === 0 && !e.shiftKey ? "orbit" : "pan";
      button = e.button;
      dragged = false;
      lastX = e.clientX;
      lastY = e.clientY;
      c.setPointerCapture(e.pointerId);
    });
    c.addEventListener("pointerup", (e) => {
      if (mode === null) return;
      mode = null;
      c.releasePointerCapture(e.pointerId);
      // A press that never became a drag is a click. Which click it is
      // depends on the button: left picks a net, right asks for the menu.
      if (dragged) return;
      if (button === 0) this.handleClick(e.clientX, e.clientY);
      else if (button === 2) this.onNetContext?.(e.clientX, e.clientY, this.pickNetAt(e.clientX, e.clientY));
    });
    c.addEventListener("pointermove", (e) => {
      if (mode === null) {
        // Not dragging: remember where the pointer is and let `render` do at
        // most one raycast a frame for it. Raycasting here instead would run
        // several times per frame on a fast move, against every instanced
        // layer, for an answer that can only be drawn once.
        this.hoverPoint = { x: e.clientX, y: e.clientY };
        return;
      }
      const dx = e.clientX - lastX;
      const dy = e.clientY - lastY;
      if (Math.abs(dx) > 2 || Math.abs(dy) > 2) dragged = true;
      lastX = e.clientX;
      lastY = e.clientY;
      if (mode === "pan") {
        this.pan(dx, dy);
        return;
      }
      this.azimuth -= dx * 0.006;
      // Both angles wrap; neither is clamped. Kept in (-PI, PI] only so the
      // numbers stay small over a long session, which changes no orientation.
      this.elevation = wrapAngle(this.elevation + dy * 0.006);
      this.azimuth = wrapAngle(this.azimuth);
      this.updateCamera();
    });
    c.addEventListener("pointerleave", () => {
      this.hoverPoint = null;
      if (this.hoveredNet < 0) return;
      this.hoveredNet = -1;
      this.onNetHover?.(null);
    });
    // The browser's own menu would cover the die and offer nothing useful
    // over a canvas; `pointerup` raises ours instead.
    c.addEventListener("contextmenu", (e) => e.preventDefault());
    c.addEventListener(
      "wheel",
      (e) => {
        e.preventDefault();
        this.radius = Math.min(
          Math.max(this.radius * Math.exp(e.deltaY * 0.001), 0.5),
          (this.stackHeightUm + this.dieWidthUm + this.dieHeightUm) * 20 + 100,
        );
        this.updateCamera();
      },
      { passive: false },
    );
  }

  /**
   * Which net (if any) the pointer is over: raycast the visible layer
   * meshes and resolve the hit instance's net id. The 3D counterpart of
   * `DieView.pickNet`, and the one place this view walks the scene.
   */
  pickNetAt(clientX: number, clientY: number): NetHit3D | null {
    const rect = this.canvas.getBoundingClientRect();
    const ndc = new THREE.Vector2(
      ((clientX - rect.left) / rect.width) * 2 - 1,
      -((clientY - rect.top) / rect.height) * 2 + 1,
    );
    this.raycaster.setFromCamera(ndc, this.camera);
    const targets = [...this.layers.values()]
      .filter((lm) => this.enabled.has(lm.name))
      .map((lm) => lm.mesh);
    const hits = this.raycaster.intersectObjects(targets, false);
    if (hits.length === 0) return null;
    const hit = hits[0];
    const lm = [...this.layers.values()].find((l) => l.mesh === hit.object);
    const instanceId = hit.instanceId;
    if (!lm || instanceId === undefined) return null;
    const netId = lm.netIds[instanceId];
    if (netId < 0) return null;
    return { id: netId, name: this.netNameById.get(netId) ?? `n${netId}` };
  }

  /** Click-to-select: the pick, published for the panel to pin. */
  private handleClick(clientX: number, clientY: number): void {
    this.onNetPick?.(this.pickNetAt(clientX, clientY));
  }

  /** At most one raycast per frame, and none at all while a drag is in
   *  progress -- orbiting is the frame budget's worst case and the pointer
   *  is not asking about a wire then anyway. */
  private updateHover(): void {
    const point = this.hoverPoint;
    if (point === null) return;
    this.hoverPoint = null;
    const hit = this.pickNetAt(point.x, point.y);
    const id = hit?.id ?? -1;
    if (id === this.hoveredNet) return;
    this.hoveredNet = id;
    this.onNetHover?.(hit ? { hit, x: point.x, y: point.y } : null);
  }

  /** Set by the panel to hear clicks; kept as a plain field rather than an
   *  event emitter since there is exactly one subscriber. */
  onNetPick: ((hit: NetHit3D | null) => void) | null = null;

  /** Fired when the net under the pointer changes, with the cursor position
   *  the answer was computed for -- the tooltip has to be placed somewhere,
   *  and by the time this fires the event is long gone. */
  onNetHover: ((hover: { hit: NetHit3D; x: number; y: number } | null) => void) | null =
    null;

  /** Fired on a right-click that was not a pan, with whatever was under it. */
  onNetContext: ((x: number, y: number, hit: NetHit3D | null) => void) | null = null;

  // ---- layers -----------------------------------------------------------

  setLayer(name: string, on: boolean): void {
    if (on) this.enabled.add(name);
    else this.enabled.delete(name);
    const lm = this.layers.get(name);
    if (lm) lm.mesh.visible = this.enabled.has(name);
  }

  isLayerOn(name: string): boolean {
    return this.enabled.has(name);
  }

  layerNames(): string[] {
    return [...this.layers.keys()];
  }

  instanceCount(): number {
    let n = 0;
    for (const lm of this.layers.values()) n += lm.mesh.count;
    return n;
  }

  // ---- net highlight ------------------------------------------------------

  netIdOf(name: string): number | null {
    return this.netIdByName.get(name) ?? null;
  }

  /** Unlike the 2D view's one-uniform highlight, this rewrites every
   *  instance's colour, so it is guarded: hovering across the die must not
   *  repaint the whole stack for a net it is already showing, and a trace
   *  sweep must not be cancelled by a hover that lands back on its own net. */
  setHighlightNet(id: number | null): void {
    const next = id ?? -1;
    if (next === this.highlightedNet) return;
    this.highlightedNet = next;
    this.traceState = null;
    this.traceActiveLayer = null;
    this.traceLabel = null;
    this.refreshHighlightColours();
  }

  get highlightedNetId(): number | null {
    return this.highlightedNet < 0 ? null : this.highlightedNet;
  }

  private refreshHighlightColours(): void {
    for (const lm of this.layers.values()) {
      const mesh = lm.mesh;
      if (this.highlightedNet < 0) {
        for (let i = 0; i < lm.netIds.length; i++) mesh.setColorAt(i, lm.baseColour);
      } else {
        const warm = new THREE.Color(HIGHLIGHT_COLOUR);
        for (let i = 0; i < lm.netIds.length; i++) {
          mesh.setColorAt(i, lm.netIds[i] === this.highlightedNet ? warm : lm.dimColour);
        }
      }
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    }
  }

  // ---- exploded view ------------------------------------------------------

  /** 0 = real stack, 1 = fully pulled apart. Only ever moves each layer's
   *  `position.z` -- no instance buffer touched. */
  setExplode(factor: number): void {
    this.explodeFactor = Math.min(1, Math.max(0, factor));
    for (const lm of this.layers.values()) {
      lm.mesh.position.z =
        this.explodeFactor * lm.index * EXPLODE_SEPARATION_UM * this.zScale;
    }
  }

  get explode(): number {
    return this.explodeFactor;
  }

  // ---- cross-section plane ------------------------------------------------

  setSectionEnabled(on: boolean): void {
    this.sectionEnabled = on;
    this.applyClipping();
  }

  get sectionOn(): boolean {
    return this.sectionEnabled;
  }

  /** 0..1 across the die's width; the plane keeps the low-x side visible and
   *  clips the rest, so dragging it sweeps a cross-section through the stack. */
  setSectionPosition(t: number): void {
    this.sectionT = Math.min(1, Math.max(0, t));
    this.applyClipping();
  }

  get sectionPosition(): number {
    return this.sectionT;
  }

  private applyClipping(): void {
    const planes = this.sectionEnabled ? [this.plane] : [];
    if (this.sectionEnabled) {
      const halfW = this.dieWidthUm / 2;
      const x = -halfW + this.sectionT * this.dieWidthUm;
      // Plane keeps points where dot(normal, p) + constant >= 0. normal =
      // (-1,0,0), so points with p.x <= x survive and the rest is clipped.
      this.plane.constant = x;
    }
    for (const lm of this.layers.values()) lm.material.clippingPlanes = planes;
  }

  // ---- net tracing (delight, not core mechanics) ---------------------------

  /** Sequential highlight sweep from the lowest layer this net touches to
   *  the highest, ~450ms per layer. Not a camera flythrough -- a simpler,
   *  cheaper sweep that still reads as "the signal climbing the stack". */
  traceNet(netId: number): void {
    this.setHighlightNet(netId);
    const order = this.layerOrder.filter((name) => {
      const lm = this.layers.get(name);
      return lm ? lm.netIds.includes(netId) : false;
    });
    if (order.length === 0) return;
    this.traceState = { netId, order, startTime: performance.now() };
    this.traceActiveLayer = null;
  }

  get tracing(): boolean {
    return this.traceState !== null;
  }

  /** Human-readable sweep status for the panel's stats readout. */
  traceStatus(): string | null {
    return this.traceLabel;
  }

  private updateTrace(now: number): void {
    if (!this.traceState) return;
    const { netId, order, startTime } = this.traceState;
    const idx = Math.floor((now - startTime) / TRACE_STEP_MS);
    if (idx >= order.length) {
      this.traceState = null;
      this.traceActiveLayer = null;
      this.traceLabel = null;
      this.refreshHighlightColours();
      return;
    }
    const activeLayer = order[idx];
    if (activeLayer === this.traceActiveLayer) return;
    this.traceActiveLayer = activeLayer;
    this.traceLabel = `tracing net ${this.netNameById.get(netId) ?? netId}: ${activeLayer} (${idx + 1}/${order.length})`;
    this.refreshHighlightColours();
    const lm = this.layers.get(activeLayer);
    if (!lm) return;
    const pulse = new THREE.Color(PULSE_COLOUR);
    for (let i = 0; i < lm.netIds.length; i++) {
      if (lm.netIds[i] === netId) lm.mesh.setColorAt(i, pulse);
    }
    if (lm.mesh.instanceColor) lm.mesh.instanceColor.needsUpdate = true;
  }

  // ---- drawing -------------------------------------------------------------

  render(): void {
    const now = performance.now();
    this.updateTrace(now);
    this.updateHover();

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(1, Math.round(this.canvas.clientWidth));
    const h = Math.max(1, Math.round(this.canvas.clientHeight));
    const targetW = Math.round(w * dpr);
    const targetH = Math.round(h * dpr);
    if (this.canvas.width !== targetW || this.canvas.height !== targetH) {
      this.renderer.setPixelRatio(dpr);
      this.renderer.setSize(w, h, false);
    }
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.render(this.scene, this.camera);
  }

  rendererInfo(): string {
    const gl = this.renderer.getContext();
    const ext = gl.getExtension("WEBGL_debug_renderer_info");
    if (!ext) return gl.getParameter(gl.RENDERER) as string;
    return `${gl.getParameter(ext.UNMASKED_VENDOR_WEBGL)} / ${gl.getParameter(
      ext.UNMASKED_RENDERER_WEBGL,
    )}`;
  }

  dispose(): void {
    for (const lm of this.layers.values()) {
      lm.material.dispose();
      this.scene.remove(lm.mesh);
    }
    this.layers.clear();
    this.geometry?.dispose();
    this.geometry = null;
    this.renderer.dispose();
  }
}

export { webgl2Available };