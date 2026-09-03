"""Bake a render bundle for a GDS, for the web die view to fetch.

Usage: uv run python scripts/bake_render.py samples/puzzle.gds samples/puzzle.render.bin
"""

import pathlib
import sys
import time

from gdsx import api


def main() -> int:
    src = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "samples/puzzle.gds")
    out = pathlib.Path(
        sys.argv[2] if len(sys.argv) > 2 else src.with_suffix(".render.bin")
    )

    t0 = time.perf_counter()
    opened = api.open_design(src.read_bytes())
    import json

    env = json.loads(opened)
    if not env["ok"]:
        print(env["error"], file=sys.stderr)
        return 1
    handle = env["data"]["handle"]

    blob = api.render_bundle(handle)
    if blob[:1] == b"{":
        print(blob.decode("utf-8"), file=sys.stderr)
        return 1

    out.write_bytes(blob)
    dt = time.perf_counter() - t0
    print(f"{out}: {len(blob):,} bytes in {dt:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
