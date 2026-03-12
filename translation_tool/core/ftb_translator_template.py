from __future__ import annotations

import os
import shutil


def prepare_ftbquests_lang_template_only_impl(
    input_config_dir: str,
    output_config_dir: str,
    *,
    prefer_lang: str = "zh_cn",
) -> dict:
    """只把 FTB Quests 的模板語系補到輸出資料夾。"""
    src_ftb = os.path.join(input_config_dir, "ftbquests")
    dst_ftb = os.path.join(output_config_dir, "ftbquests")

    if not os.path.isdir(src_ftb):
        raise FileNotFoundError(f"找不到來源 ftbquests: {src_ftb}")

    src_lang_root = os.path.join(src_ftb, "quests", "lang")
    dst_lang_root = os.path.join(dst_ftb, "quests", "lang")
    os.makedirs(dst_lang_root, exist_ok=True)

    prefer = prefer_lang.lower()
    src_prefer_dir = os.path.join(src_lang_root, prefer)
    src_fallback_dir = os.path.join(src_lang_root, "en_us")

    if os.path.isdir(src_prefer_dir) or os.path.isdir(src_fallback_dir):
        src_dir = src_prefer_dir if os.path.isdir(src_prefer_dir) else src_fallback_dir
        dst_dir = os.path.join(dst_lang_root, os.path.basename(src_dir))
        shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
        return {
            "mode": "dir",
            "template_used": src_dir,
            "template_copied_to": dst_dir,
        }

    src_prefer_file = os.path.join(src_lang_root, f"{prefer}.snbt")
    src_fallback_file = os.path.join(src_lang_root, "en_us.snbt")

    if os.path.isfile(src_prefer_file) or os.path.isfile(src_fallback_file):
        src_file = src_prefer_file if os.path.isfile(src_prefer_file) else src_fallback_file
        dst_file = os.path.join(dst_lang_root, os.path.basename(src_file))
        os.makedirs(os.path.dirname(dst_file), exist_ok=True)
        shutil.copy2(src_file, dst_file)
        return {
            "mode": "file",
            "template_used": src_file,
            "template_copied_to": dst_file,
        }

    raise FileNotFoundError(
        "找不到 FTB Quests 模板語系（資料夾制/單檔制都沒有）:\n"
        f"- dir: {src_prefer_dir}\n"
        f"- dir: {src_fallback_dir}\n"
        f"- file: {src_prefer_file}\n"
        f"- file: {src_fallback_file}\n"
    )
