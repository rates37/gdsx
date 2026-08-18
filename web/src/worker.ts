// Runs in a dedicated Web Worker so extraction never blocks the UI thread.
// Boots Pyodide from same-origin files, micropip-installs the gdsx wheel
// from a same-origin URL (no CDN in the shipped page), and exposes
// `gdsx.api` over Comlink.
import { expose } from "comlink";
import { loadPyodide, type PyodideInterface } from "pyodide";

export interface Envelope<T = unknown> {
  schema_version: number;
  ok: boolean;
  data?: T;
  error?: { code: string; message: string; detail?: unknown };
}

let pyodideReady: Promise<PyodideInterface> | null = null;

async function boot(): Promise<PyodideInterface> {
  const pyodide = await loadPyodide({ indexURL: "/pyodide/" });
  // pyyaml is gdsx's one hard runtime dependency. Load it through Pyodide's
  // own package loader (which resolves against pyodide-lock.json at the same
  // origin) rather than letting micropip go looking on PyPI for it -- PyPI
  // only has manylinux wheels for pyyaml, which micropip correctly refuses
  // to install into a wasm runtime.
  await pyodide.loadPackage(["micropip", "pyyaml"]);
  const micropip = pyodide.pyimport("micropip");
  // pyyaml is already satisfied by loadPackage above, so this resolves no
  // further dependencies over the network -- everything comes from the
  // same-origin /wheel/ and /pyodide/ URLs.
  await micropip.install(`${self.location.origin}/wheel/gdsx-0.1.0-py3-none-any.whl`, {
    deps: true,
  });
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

const worker = {
  async ready(): Promise<void> {
    await getPyodide();
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
};

export type GdsxWorker = typeof worker;

expose(worker);