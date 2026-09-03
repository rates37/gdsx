// Runs in a dedicated Web Worker so extraction never blocks the UI thread.
// Boots Pyodide from same-origin files, micropip-installs the gdsx wheel
// from a same-origin URL (no CDN in the shipped page), and exposes
// `gdsx.api` over Comlink.
import { expose } from "comlink";
import { loadPyodide, type PyodideInterface } from "pyodide";

import { assetUrl } from "./asset-url.ts";

export interface Envelope<T = unknown> {
  schema_version: number;
  ok: boolean;
  data?: T;
  error?: { code: string; message: string; detail?: unknown };
}

let pyodideReady: Promise<PyodideInterface> | null = null;

async function boot(): Promise<PyodideInterface> {
  const pyodide = await loadPyodide({ indexURL: assetUrl("pyodide/") });
  // pyyaml is gdsx's one hard runtime dependency. Load it through Pyodide's
  // own package loader (which resolves against pyodide-lock.json at the same
  // origin) rather than letting micropip go looking on PyPI for it -- PyPI
  // only has manylinux wheels for pyyaml, which micropip correctly refuses
  // to install into a wasm runtime.
  await pyodide.loadPackage(["micropip", "pyyaml"]);
  const micropip = pyodide.pyimport("micropip");
  // The wheel's filename embeds the package version, so it is not something
  // this file can spell: bumping `version` in pyproject.toml would break the
  // app at runtime with a build that still passed. scripts/sync-assets.mjs
  // writes the name it actually copied into this manifest.
  const manifestUrl = assetUrl("wheel/manifest.json");
  const manifestResponse = await fetch(manifestUrl);
  if (!manifestResponse.ok) {
    throw new Error(
      `gdsx: no wheel manifest at ${manifestUrl} ` +
        `(${manifestResponse.status}) -- run \`npm run sync-assets\``,
    );
  }
  const { wheel } = (await manifestResponse.json()) as { wheel: string };
  // pyyaml is already satisfied by loadPackage above, so this resolves no
  // further dependencies over the network -- everything comes from the
  // same-origin wheel/ and pyodide/ URLs. The URL is base-relative rather
  // than origin-absolute: under a sub-path deploy `self.location.origin`
  // drops the base and micropip 404s.
  await micropip.install(assetUrl(`wheel/${wheel}`), { deps: true });
  return pyodide;
}

// A JS `Uint8Array` arrives in Python as a JsProxy, not as `bytes`, so
// `api.open_design(gds_bytes)` fails with "a bytes-like object is required,
// not 'pyodide.ffi.JsProxy'". Copy it into real `bytes` at the boundary --
// the endpoints that take binary (`open_design`, `cache_key`) all want
// `bytes`, and this is the only place that knows it crossed from JS.
function toPython(arg: unknown, pyodide: PyodideInterface): unknown {
  if (arg instanceof ArrayBuffer) arg = new Uint8Array(arg);
  if (arg instanceof Uint8Array) {
    // `to_bytes()` is the JsProxy buffer method; it copies into real `bytes`.
    const asBytes = pyodide.runPython(
      "lambda buf: buf.to_bytes() if hasattr(buf, 'to_bytes') else bytes(buf)",
    ) as (x: unknown) => unknown;
    return asBytes(arg);
  }
  return arg;
}

function getPyodide(): Promise<PyodideInterface> {
  if (!pyodideReady) pyodideReady = boot();
  return pyodideReady;
}

export interface ReplResult {
  ok: boolean;
  stdout: string;
  /** `repr()` of the last expression's value, or "" for a statement / None. */
  repr: string;
  /** True when `repr` is a JSON literal worth pretty-printing rather than
   *  showing as Python repr text -- set for the handful of return shapes the
   *  REPL bootstrap recognises (see `REPL_BOOTSTRAP`'s `_gdsx_repl_render`). */
  rendered: string | null;
  error: string | null;
}

// Defines `_gdsx_repl_eval(handle, source)` once, in Pyodide's global
// namespace, rather than re-building a multi-line exec/eval dance from JS on
// every keystroke. `_gdsx_repl_bindings` below is the one statement of what is
// in scope; the panel prints that list rather than carrying its own copy.
//
// The namespace those names live in is created once per design and kept for
// the session. It used to be rebuilt on every call, which meant a name the
// player bound -- `a = 1`, a helper function, an intermediate result -- was
// written into a dict that was thrown away before the next prompt, so the
// following line could not see it. A console you cannot build anything up
// in is a calculator, and everything interesting to ask about a netlist
// takes more than one line.
//
// "Rich output" here is deliberately modest: a `Netlist`/trace-shaped object
// gets pretty-printed as JSON via `gdsx.core.serial.to_dict` when it is one
// of the dataclasses that module already knows how to flatten (the same
// serialiser every `gdsx.api` endpoint uses -- not a second renderer);
// everything else falls back to `repr()`, honestly, rather than pretending
// to have a table view for an arbitrary Python object.
const REPL_BOOTSTRAP = `
_GDSX_REPL_NAMESPACES = {}


def _gdsx_repl_bindings(_handle):
    """The names the panel promises are in scope, freshly resolved."""
    import gdsx.api as _api

    _design = _api._get(_handle)
    return {
        "nl": _design.netlist,
        "graph": _design.graph,
        "sim": _design.simulator(),
        "design": _design,
        "api": _api,
    }


def _gdsx_repl_namespace(_handle):
    """This design's session namespace, created on first use.

    Keyed by handle so opening a different puzzle starts clean rather than
    inheriting names bound against a netlist that is no longer loaded.
    """
    _ns = _GDSX_REPL_NAMESPACES.get(_handle)
    if _ns is None:
        _ns = _gdsx_repl_bindings(_handle)
        # The way back from \`nl = None\`. Everything else in here a player
        # can rebuild by typing it again; the design bindings they cannot,
        # and losing them to a typo would mean reloading the page.
        _ns["reset"] = lambda: _ns.update(_gdsx_repl_bindings(_handle))
        _GDSX_REPL_NAMESPACES[_handle] = _ns
    return _ns


def _gdsx_repl_names(_handle):
    """The names in scope, as JSON, for the panel to show the player.

    Read off the live namespace rather than written down again in TypeScript:
    the bindings were listed in three places -- this module's header, the
    panel's placeholder and the guided walkthrough -- and all three had drifted
    to a different subset of the truth. Two of those were prose and stay prose;
    the one the player actually reads while typing now comes from here.
    """
    import json

    return json.dumps(sorted(_gdsx_repl_namespace(_handle)))


def _gdsx_repl_eval(_handle, _source):
    import ast, io, contextlib, json
    from gdsx.core import serial as _serial

    _ns = _gdsx_repl_namespace(_handle)
    _buf = io.StringIO()
    _value = None
    _error = None
    try:
        _body = ast.parse(_source, "<repl>", "exec").body
        # A trailing expression is evaluated rather than executed, so its
        # value can be shown -- that is the difference between a console and
        # a script runner, and it has to survive the statements before it in
        # a pasted block.
        _tail = _body.pop() if _body and isinstance(_body[-1], ast.Expr) else None
        with contextlib.redirect_stdout(_buf):
            if _body:
                exec(compile(ast.Module(body=_body, type_ignores=[]), "<repl>", "exec"), _ns)
            if _tail is not None:
                _value = eval(compile(ast.Expression(body=_tail.value), "<repl>", "eval"), _ns)
    except Exception as exc:  # noqa: BLE001 -- shown to the player, not raised
        _error = f"{type(exc).__name__}: {exc}"

    _repr = "" if _value is None else repr(_value)
    _rendered = None
    import dataclasses as _dataclasses
    if _value is not None and _dataclasses.is_dataclass(_value):
        try:
            _rendered = json.dumps(_serial.to_dict(_value), indent=2)
        except TypeError:
            _rendered = None
    # A JSON string, not a Python tuple -- returning a tuple across the FFI
    # boundary hands back a PyProxy, not a destructurable JS array. Every
    # gdsx.api endpoint already sidesteps this the same way (see _envelope).
    return json.dumps(
        {"stdout": _buf.getvalue(), "repr": _repr, "rendered": _rendered, "error": _error}
    )
`;

const worker = {
  async ready(): Promise<void> {
    const pyodide = await getPyodide();
    pyodide.runPython(REPL_BOOTSTRAP);
  },

  /** Call any `gdsx.api.<name>(...)` endpoint; every one returns a JSON envelope string. */
  async call(name: string, ...args: unknown[]): Promise<Envelope> {
    const pyodide = await getPyodide();
    const api = pyodide.pyimport("gdsx.api");
    const fn = api[name];
    if (fn === undefined) throw new Error(`gdsx.api has no endpoint '${name}'`);
    const result: string = fn(...args.map((a) => toPython(a, pyodide)));
    return JSON.parse(result) as Envelope;
  },

  /** The names bound in `handle`'s REPL session, sorted. What the panel
   *  prints above the prompt, so the advertised scope cannot drift from the
   *  real one. */
  async replBindings(handle: string): Promise<string[]> {
    const pyodide = await getPyodide();
    const fn = pyodide.globals.get("_gdsx_repl_names") as (handle: string) => string;
    try {
      return JSON.parse(fn(handle)) as string[];
    } catch {
      // The panel is fully usable without the line; it just does not get to
      // say what is in scope.
      return [];
    }
  },

  /** Run one REPL entry against the open design at `handle`. Never throws --
   *  a Python-side exception comes back as `{ ok: false, error }`, same
   *  contract as `call`'s envelope, just for free-form source instead of a
   *  named endpoint. */
  async evalPython(handle: string, source: string): Promise<ReplResult> {
    const pyodide = await getPyodide();
    const fn = pyodide.globals.get("_gdsx_repl_eval") as (handle: string, source: string) => string;
    try {
      const parsed = JSON.parse(fn(handle, source)) as {
        stdout: string;
        repr: string;
        rendered: string | null;
        error: string | null;
      };
      return { ok: parsed.error === null, ...parsed };
    } catch (err) {
      return {
        ok: false,
        stdout: "",
        repr: "",
        rendered: null,
        error: err instanceof Error ? err.message : String(err),
      };
    }
  },
};

export type GdsxWorker = typeof worker;

expose(worker);