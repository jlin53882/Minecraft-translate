"""Locate runtime-provided Minecraft assets next to a selected mods directory."""

import json
from pathlib import Path


def get_runtime_asset_paths(source_root: Path) -> list[Path]:
    """Return the client/profile/NeoForge files that affect icon resolution."""
    source_root = Path(source_root).resolve()
    profile_dir = source_root.parent
    paths = [
        profile_dir / f"{profile_dir.name}.jar",
        profile_dir / f"{profile_dir.name}.json",
    ]
    if len(source_root.parents) < 3:
        return [path for path in paths if path.is_file()]

    profile_json = paths[1]
    try:
        profile = json.loads(profile_json.read_text(encoding="utf-8"))
        game_args = profile.get("arguments", {}).get("game", [])
        version_index = game_args.index("--fml.neoForgeVersion")
        loader_version = game_args[version_index + 1]
        if not isinstance(loader_version, str) or not loader_version:
            return [path for path in paths if path.is_file()]
    except (OSError, ValueError, IndexError, TypeError, AttributeError):
        return [path for path in paths if path.is_file()]

    loader_jar = (
        source_root.parents[2]
        / "libraries"
        / "net"
        / "neoforged"
        / "neoforge"
        / loader_version
        / f"neoforge-{loader_version}-universal.jar"
    )
    if loader_jar.is_file():
        paths.append(loader_jar)
    return [path for path in paths if path.is_file()]
