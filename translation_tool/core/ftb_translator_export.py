from __future__ import annotations

import os
from pathlib import Path
from typing import Callable


def resolve_ftbquests_quests_root_impl(base_dir: str) -> str:
    """往下遞迴找 config/ftbquests/quests。"""
    base = Path(base_dir).expanduser().resolve()

    direct = base / "ftbquests" / "quests"
    if direct.is_dir():
        return str(direct)

    candidates = list(base.rglob("config/ftbquests/quests"))
    if not candidates:
        for p in base.rglob("*"):
            if p.is_dir():
                parts = [x.lower() for x in p.parts[-3:]]
                if parts == ["ftbquests", "quests"] and len(p.parts) >= 3 and p.parts[-3].lower() == "config":
                    candidates.append(p)

    if not candidates:
        raise FileNotFoundError(f"找不到 config\\ftbquests\\quests (base_dir={base})")

    candidates.sort(key=lambda p: (len(p.parts), str(p)))
    return str(candidates[0])


def export_ftbquests_raw_json_impl(
    base_dir: str,
    *,
    output_dir: str | None = None,
    resolve_ftbquests_quests_root_fn: Callable[[str], str],
    process_quest_folder_fn: Callable[[str], dict],
    orjson_dump_file_fn: Callable[[object, object], None],
    log_info_fn: Callable[..., None],
) -> dict:
    """抽取 FTB raw JSON 到 Output/ftbquests/raw/...。"""
    quests_root = resolve_ftbquests_quests_root_fn(base_dir)
    extracted = process_quest_folder_fn(quests_root)

    out_root = output_dir or os.path.join(base_dir, "Output")
    raw_root = os.path.join(
        out_root, "ftbquests", "raw", "config", "ftbquests", "quests", "lang"
    )
    os.makedirs(raw_root, exist_ok=True)

    written_langs: list[str] = []
    for lang, data in extracted.items():
        if not data.get("lang") and not data.get("quests"):
            continue

        lang_dir = os.path.join(raw_root, lang)
        os.makedirs(lang_dir, exist_ok=True)

        with open(os.path.join(lang_dir, "ftb_lang.json"), "wb") as f:
            orjson_dump_file_fn(data.get("lang", {}), f)
        with open(os.path.join(lang_dir, "ftb_quests.json"), "wb") as f:
            orjson_dump_file_fn(data.get("quests", {}), f)

        written_langs.append(lang)

    log_info_fn("FTB raw 輸出語言：%s", written_langs)
    return {
        "raw_root": raw_root,
        "out_root": out_root,
        "quests_root": quests_root,
        "written_langs": written_langs,
    }
