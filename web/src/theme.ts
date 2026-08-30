// The palette, for the four surfaces that are drawn rather than styled.
//
// The waveform, the experiment matrix, the minimap and the die view paint into
// a canvas or a shader, where a stylesheet cannot reach them. Without this they
// each carried their own hex literals, and a palette change had to be made in
// five places and would silently miss one.
//
// This is not a third home for styles: nothing here defines a colour. Every
// value is read back out of `styles/tokens.css`, which stays the single place
// a colour is chosen. If a token is missing the fallback is a visible grey
// rather than black, so the mistake shows up on screen instead of vanishing
// into the background.

/** Read a `--df-*` token off the document root. */
function token(name: string, fallback = "#808080"): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

/**
 * The tokens the drawing code needs, resolved once.
 *
 * Lazy rather than computed at module load: the stylesheet is imported by
 * `main.ts` and a module evaluated before it would read every token as an
 * empty string. Cached after the first call -- these do not change at runtime,
 * there being one theme.
 */
let cache: Palette | null = null;

export interface Palette {
  void: string;
  plate: string;
  edge: string;
  ink3: string;
  dim: string;
  faint2: string;
  teal: string;
  coral: string;
  gold: string;
  blue: string;
  cyan: string;
  /** Die view layer fills, keyed by the render bundle's layer names. */
  layer: Record<string, string>;
}

export function palette(): Palette {
  if (cache) return cache;
  cache = {
    void: token("--df-void", "#0c0d10"),
    plate: token("--df-plate", "#131519"),
    edge: token("--df-edge", "#21242a"),
    ink3: token("--df-ink-3", "#a5a5a1"),
    dim: token("--df-dim", "#85878e"),
    faint2: token("--df-faint-2", "#4e5057"),
    teal: token("--df-teal", "#59e0c8"),
    coral: token("--df-coral", "#ff7a59"),
    gold: token("--df-gold", "#ffd166"),
    blue: token("--df-blue", "#8ab4ff"),
    cyan: token("--df-cyan", "#6ad9e6"),
    layer: {
      instances: token("--df-layer-cells", "#8c94a8"),
      li1: token("--df-layer-li1", "#6bbf6b"),
      met1: token("--df-layer-met1", "#598cf2"),
      met2: token("--df-layer-met2", "#f2735a"),
      met3: token("--df-layer-met3", "#f2cc4d"),
      met4: token("--df-layer-met4", "#bf66e6"),
      met5: token("--df-layer-met5", "#66e6e6"),
    },
  };
  return cache;
}

/** The mono stack, for `ctx.font`. Canvas takes a font shorthand, not a var(). */
export const CANVAS_MONO = '"Spline Sans Mono", ui-monospace, Menlo, monospace';