#!/usr/bin/env python3
"""Build deterministic layered StackChan avatar artifacts."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stackchan.avatar_assets import write_avatar_assets


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the deterministic XC Body layered avatar set."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "build" / "avatar-assets",
        help="artifact directory (default: build/avatar-assets)",
    )
    args = parser.parse_args(argv)
    paths = write_avatar_assets(args.output_dir)
    for name in ("payload", "manifest", "preview"):
        print(f"{name}: {paths[name]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
