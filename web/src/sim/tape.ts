// Parses the compiled gate tape: the same self-contained bundle format
// render.bin uses (uint32 header length, UTF-8 JSON header, binary blob) --
// see scripts/bake_tape.py and gdsx.puzzle._tape_bundle. The blob is the op
// stream as little-endian int32, stride 6: [opcode, out, in0, in1, in2, in3]
// per op, topologically ordered -- gdsx.sim.tape.GateTape.to_bytes().

export interface FlopDef {
  d: number;
  q: number;
  clk: number;
  rst: number;
  set: number;
  /** 0=DFF 1=DFFR 2=DFFS 3=DFFSR 4=DLATCH; informational, the executor never branches on it. */
  kind: number;
}

export interface TapeHeader {
  tape_version: number;
  n_nets: number;
  n_flops: number;
  n_ops: number;
  /** primary input net ids, in net-name order */
  inputs: number[];
  flops: FlopDef[];
  /** (net id, 0|1) pairs, seeded before any input or op runs */
  consts: [number, number][];
  /** net name -> net id. Not every net is named -- the compiler's own temporaries are not. */
  names: Record<string, number>;
  /** instance name per entry in `flops`, same order */
  flop_names: string[];
}

export interface GateTape {
  header: TapeHeader;
  /** stride 6: [opcode, out, in0, in1, in2, in3] per op. */
  ops: Int32Array;
}

export function parseTapeBundle(data: ArrayBuffer): GateTape {
  const headerLen = new DataView(data).getUint32(0, true);
  const json = new TextDecoder().decode(new Uint8Array(data, 4, headerLen));
  const header = JSON.parse(json) as TapeHeader;
  // A fresh ArrayBuffer starting at 0, so the Int32Array below is guaranteed
  // 4-byte aligned -- `4 + headerLen` is not, in general.
  const blob = data.slice(4 + headerLen);
  return { header, ops: new Int32Array(blob) };
}