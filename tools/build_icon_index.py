"""Build a persistent prebuilt icon index for a modpack's mods directory."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.icon_index import build_and_save_icon_index, get_index_path  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prebuild the icon index used by Minecraft-translate's mod database."
    )
    parser.add_argument(
        "mods_dir", type=Path, help="Directory containing mod JAR files"
    )
    args = parser.parse_args(argv)
    mods_dir = args.mods_dir.expanduser().resolve()
    if not mods_dir.is_dir():
        parser.error(f"mods directory does not exist: {mods_dir}")

    jars = list(mods_dir.glob("*.jar"))
    if not jars:
        parser.error(f"no .jar files found in: {mods_dir}")

    print(f"Building icon index for {len(jars)} JARs in {mods_dir}...")
    index = build_and_save_icon_index(mods_dir)
    print(f"Saved {len(index)} icon entries to {get_index_path(mods_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
