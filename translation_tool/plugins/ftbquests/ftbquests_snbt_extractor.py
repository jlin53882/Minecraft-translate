"""translation_tool/plugins/ftbquests/ftbquests_snbt_extractor.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

# ftbquests_snbt_extractor.py
# FTB Quests SNBT 抽取工具
import os
import re
import json
import ftb_snbt_lib as snbt

from ftb_snbt_lib.tag import Compound, List

from translation_tool.utils.log_unit import (
    log_info,
    log_error,
    log_warning,
)

# =========================
# 語系設定
# =========================
LANG_WHITELIST = ["en_us", "zh_cn", "zh_tw"]
LANG_PRIORITY = {lang: i for i, lang in enumerate(LANG_WHITELIST)}

# 只解析語言字串 key
LANG_KEY_SUFFIX = (".title", ".quest_desc")

def is_lang_key_ref(val: str):
    # 遇到 {ftbquests.xxx} 這種語言 reference 直接跳過
    """

    """
    return bool(re.match(r"^\{ftbquests\.", val))

def is_lang_key_ref_like(val: str) -> bool:
    """
    過濾純引用格式：
    - {atm9.quest.create.desc.belts.1}
    - {atm9.quest.create.desc.belts.1}\n{atm9.quest.create.desc.belts.2}
    """
    if not isinstance(val, str):
        return False
    s = val.strip()
    if not s:
        return False
    return bool(re.fullmatch(r"\{[^{}]+\}(?:\n\{[^{}]+\})*", s))

TAG_CONDITION_PATTERN = re.compile(
    r"^\s*(any\s+of|any|all|no)\s*#",
    re.IGNORECASE,
)

def is_tag_condition_text(s: str) -> bool:
    """
    判斷是否為 FTB tag 條件文字，例如：
    - Any #minecraft:logs
    - Any#forge:ingots/iron
    - All #forge:ores
    """
    return bool(TAG_CONDITION_PATTERN.match(s))

def walk_snbt_file(path: str) -> Compound | None:
    """讀取 SNBT 檔案"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return snbt.load(f)
    except Exception as e:
        log_error(f"❌ SNBT 解析失敗: {path} -> {e}")
        return None

# =========================
# lang/*.snbt 抽取
# =========================
def extract_lang_file(filename: str, root: Compound) -> dict:
    """

    """
    out = {}

    for key, val in root.items():
        if any(key.endswith(s) for s in LANG_KEY_SUFFIX):
            if isinstance(val, snbt.String):
                raw = str(val)
                if (
                    raw
                    and not is_lang_key_ref(raw)
                    and not is_lang_key_ref_like(raw)
                    and not is_tag_condition_text(raw)
                ):
                    out[f"{filename}|{key}"] = raw

            elif isinstance(val, List):
                texts = []
                for e in val:
                    if isinstance(e, snbt.String):
                        s = str(e)
                        if s and not is_lang_key_ref(s) and not is_lang_key_ref_like(s):
                            texts.append(s)

                if texts:
                    out[f"{filename}|{key}"] = "\n".join(texts)

    return out

# =========================
# quest 本體抽取（title）
# =========================
def extract_quest_file(filename: str, root: Compound) -> dict:
    """

    """
    out = {}

    def _emit(obj: Compound, field: str, kind: str):
        """發射翻譯項目。"""
        val = obj.get(field)
        if val is None:
            return

        text = None

        # String
        if isinstance(val, snbt.String):
            s = str(val)
            if s and not is_lang_key_ref(s) and not is_lang_key_ref_like(s):
                text = s

        # List[String]
        elif isinstance(val, List):
            parts = []
            for e in val:
                if isinstance(e, snbt.String):
                    s = str(e)
                    if s and not is_lang_key_ref(s) and not is_lang_key_ref_like(s):
                        parts.append(s)
                else:
                    # 非字串就跳過（保守）
                    pass
            if parts:
                text = "\n".join(parts)

        if not text:
            return

        # ✅ 新增：Tag 條件字串不抽取（避免翻譯破壞）
        if is_tag_condition_text(text):
            return

        id_val = obj.get("id")
        if isinstance(id_val, snbt.String):
            key = f"{filename}|id:{str(id_val)}|{kind}"
        else:
            key = f"{filename}|{kind}"

        out[key] = text

    def recurse(obj, path):
        """遞迴遍歷物件提取翻譯。"""
        if isinstance(obj, Compound):
            # ✅ 抽三種欄位
            _emit(obj, "title", "title")
            _emit(obj, "subtitle", "subtitle")
            _emit(obj, "description", "description")

            for sub_key, sub_val in obj.items():
                if isinstance(sub_val, (Compound, List)):
                    recurse(sub_val, f"{path}.{sub_key}")

        elif isinstance(obj, List):
            for idx, item in enumerate(obj):
                if isinstance(item, (Compound, List)):
                    recurse(item, f"{path}[{idx}]")

    recurse(root, "root")
    return out

def ensure_lang(store: dict, lang: str):
    """確保語系存在於儲存區。"""
    if lang not in store:
        store[lang] = {"lang": {}, "quests": {}}

# =========================
# 主流程
# =========================
def process_quest_folder(quests_root: str) -> dict:
    """處理任務資料夾並提取翻譯。"""
    final_output = {}
    final_output = {}
    lang_dir = os.path.join(quests_root, "lang")

    # ---------
    # 掃描可用語系
    # ---------
    available_langs = set()

    if os.path.isdir(lang_dir):
        for entry in os.listdir(lang_dir):
            p = os.path.join(lang_dir, entry)

            if os.path.isdir(p):
                available_langs.add(entry)
            elif entry.lower().endswith(".snbt"):
                available_langs.add(entry.replace(".snbt", ""))

    # ---------
    # 決定實際處理語系
    # ---------
    hit_whitelist = [lang for lang in LANG_WHITELIST if lang in available_langs]

    if hit_whitelist:
        target_langs = hit_whitelist
        # ✅ 命中白名單：只處理 en_us/zh_cn/zh_tw（若存在）
        log_info(f"🎯 命中語系白名單: {target_langs}")
    else:
        target_langs = sorted(available_langs)
        if not target_langs:
            # ✅ lang 目錄不存在或掃不到任何語系：仍需至少用 en_us 承載 quest 本體抽取結果
            target_langs = ["en_us"]
            log_warning(
                f"⚠️ 未掃到任何語系（lang 目錄不存在或為空），fallback 使用: {target_langs}"
            )
        else:
            # ✅ 沒命中白名單：改為處理全部掃到的語系（可能包含 ru_ru、ja_jp 等）
            log_warning(f"⚠️ 未命中白名單，使用全部語系: {target_langs}")

    # ---------
    # 1. 解析 lang/*.snbt
    # ---------
    if os.path.isdir(lang_dir):
        for entry in os.listdir(lang_dir):
            path = os.path.join(lang_dir, entry)

            # lang/en_us/
            if os.path.isdir(path):
                lang_code = entry
                if lang_code not in target_langs:
                    continue

                ensure_lang(final_output, lang_code)

                for subdir, _, files in os.walk(path):
                    for file in files:
                        if file.lower().endswith(".snbt"):
                            fp = os.path.join(subdir, file)
                            data = walk_snbt_file(fp)
                            if data:
                                final_output[lang_code]["lang"].update(
                                    extract_lang_file(file, data)
                                )

            # lang/en_us.snbt
            elif entry.lower().endswith(".snbt"):
                lang_code = entry.replace(".snbt", "")
                if lang_code not in target_langs:
                    continue

                ensure_lang(final_output, lang_code)

                data = walk_snbt_file(path)
                if data:
                    final_output[lang_code]["lang"].update(
                        extract_lang_file(entry, data)
                    )

    # ---------
    # 2. 解析 quest 本體（套用到所有 target_langs）
    # ---------
    for root_, _, files in os.walk(quests_root):
        for file in files:
            if not file.lower().endswith(".snbt"):
                continue

            fp = os.path.join(root_, file)

            # 跳過 lang 目錄
            if os.path.commonpath([fp, lang_dir]) == lang_dir:
                continue

            data = walk_snbt_file(fp)
            if not data:
                continue

            extracted = extract_quest_file(file, data)
            # rel_path = os.path.relpath(fp, quests_root).replace("\\", "/")
            # extracted = extract_quest_file(rel_path, data)

            for lang in target_langs:
                ensure_lang(final_output, lang)
                final_output[lang]["quests"].update(extracted)

    return final_output

# =========================
# CLI 入口
# =========================
if __name__ == "__main__":
    # ✅ CLI 提示資訊：用 log_info，方便統一進 logs
    log_info("請輸入 FTB Quests config 路徑")
    log_info(r"例如：C:\Users\...\All the Mods 10\config")

    # ✅ input 屬於互動輸入，不屬於 log（保留）
    base_config = input("路徑: ").strip().strip('"')

    if not os.path.isdir(base_config):
        # ✅ 致命錯誤：用 log_error 後退出
        log_error("❌ 路徑不存在")
        raise SystemExit(1)

    quests_root = os.path.join(base_config, "ftbquests", "quests")

    if not os.path.isdir(quests_root):
        log_error("❌ 找不到 ftbquests\\quests")
        raise SystemExit(1)

    # ✅ 顯示解析到的 quests_root
    log_info(f"📂 使用 quests 路徑: {quests_root}")

    result = process_quest_folder(quests_root)

    # ===== 輸出集中在 quests/Output =====
    lang_root = os.path.join(quests_root, "lang")
    relative_lang_path = os.path.relpath(lang_root, base_config)
    output_root = os.path.join(base_config, "Output", relative_lang_path)
    os.makedirs(output_root, exist_ok=True)

    for lang, data in result.items():
        lang_dir = os.path.join(output_root, lang)
        os.makedirs(lang_dir, exist_ok=True)

        with open(os.path.join(lang_dir, "ftb_lang.json"), "w", encoding="utf-8") as f:
            json.dump(data["lang"], f, indent=2, ensure_ascii=False)

        with open(
            os.path.join(lang_dir, "ftb_quests.json"), "w", encoding="utf-8"
        ) as f:
            json.dump(data["quests"], f, indent=2, ensure_ascii=False)

    # ✅ 成功訊息：info
    log_info("✅ 抽取完成")
    log_info(f"📤 輸出位置: {output_root}")
