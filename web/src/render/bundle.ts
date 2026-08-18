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
  instances: Slice;
  cell_names: string[];
  n_nets: number;
  net_names: Record<string, string>;
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