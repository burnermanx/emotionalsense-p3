#!/usr/bin/env python3
"""Development-only: the old whole-game pipeline (cmake + the system compiler)
that ends in a local build of the GAME. Not a distribution path: the game
may not be redistributed, and the released launcher (es_main.py) prepares the
game on the player's machine with the kit instead.

    es_dev.py --iso game.iso [--data DIR] [--only recomp,compile]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from es_pipeline import Options, Pipeline, PipelineError, default_data_dir, refuse_real_data  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True)
    ap.add_argument("--data")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only")
    a = ap.parse_args()
    refuse_real_data(a.data if a.data else default_data_dir())
    opts = Options(iso=Path(a.iso).expanduser().resolve(),
                   data_dir=Path(a.data).expanduser() if a.data else default_data_dir(),
                   force=a.force)
    try:
        out = Pipeline(opts, log=lambda s: print(s, flush=True),
                       progress=lambda f, w: sys.stderr.write(f"\r[{int(f * 100):3d}%] {w[:70]:<70}")
                       ).run(a.only.split(",") if a.only else None)
    except PipelineError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1
    print(f"\nDone: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
