// Parsing side of the L12 render bundle (`gdsx.render.RenderBundle.pack`):
// a uint32 header length, a UTF-8 JSON header, then one binary blob of
// int32 arrays that the header indexes into by (offset, count, stride).
//
// Offsets in the header are counted in int32 *elements*, not bytes.

export interface Slice {
  offset: number;
  count: number;
  stride: number;
}

export interface LayerLod {
  rects: Slice;
  shape_ids: Slice;
  /** 16*16+1 prefix offsets: rects for tile t are [tiles[t], tiles[t+1]). */
  tiles: Slice;
}

export interface StackEntry {
  name: string;
  z: number;
  thickness: number;
  via: boolean;
}

export interface RenderHeader {
  schema_version: number;
  dbu: number;
  top: string;
  bbox: [number, number, number, number];
  stack: StackEntry[];
  tile_grid: { cols: number; rows: number };
  layers: string[];
  lod_levels: number;
  lod1_min_area: number;
  lods: Record<string, Record<string, LayerLod>>;
  /** Which layers carry nets ("routing") and which are scenery ("device").
   *  Absent in schema 1 bundles, where every layer was routing. */
  layer_kind?: Record<string, "routing" | "device">;
  /** Layers that cover regions rather than drawing wires. */
  fill_layers?: string[];
  /** Layers with no GDS geometry behind them -- the 3D substrate slab. The
   *  2D view skips these: from above the substrate is an opaque rectangle
   *  the size of the die. */
  synthetic_layers?: string[];
  instances: Slice;
  cell_names: string[];
  n_nets: number;
  net_names: Record<string, string>;
  /** Net ids that tracing found but extraction dropped: metal that reaches no
   *  logic cell pin, so it has no entry in the netlist even though it is
   *  drawn, pickable, and carries an `n<id>`-shaped name like any other net.
   *  Absent in bundles baked before this field existed, where the viewer has
   *  no way to tell and must assume every net is extracted. */
  unextracted_nets?: number[];
  /** Parallel to `unextracted_nets`: the instance whose footprint wholly
   *  contains that net's metal, so the viewer can say *what* the net is
   *  ("internal wiring of dfrtp_2_4") rather than only what it is not. `""`
   *  where no single cell contains it -- top-level metal that lands on no
   *  pin. Absent in bundles baked before this field existed. */
  unextracted_owner?: string[];
  n_shapes: number;
  net_of_shape: Slice;
  net_shapes: { offsets: Slice; ids: Slice };
}

export class RenderBundle {
  constructor(
    readonly header: RenderHeader,
    readonly blob: ArrayBuffer,
  ) {}

  static parse(data: ArrayBuffer): RenderBundle {
    const headerLen = new DataView(data).getUint32(0, true);
    const json = new TextDecoder().decode(new Uint8Array(data, 4, headerLen));
    return new RenderBundle(JSON.parse(json) as RenderHeader, data.slice(4 + headerLen));
  }

  /** An Int32Array view of one header slice. No copy. */
  view(slice: Slice): Int32Array {
    return new Int32Array(this.blob, slice.offset * 4, slice.count * slice.stride);
  }
}