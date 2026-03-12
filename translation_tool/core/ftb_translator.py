"""translation_tool/core/ftb_translator.py 模組。

用途：作為 FTB pipeline 的相容入口，保留 translate/export/clean/template/orchestration 的對外契約。
維護注意：主要 helper 已拆到 ftb_translator_export / clean / template 子模組；新邏輯優先落子模組。
"""

import concurrent.futures
import math
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Generator, List

import orjson

from ..plugins.ftbquests.ftbquests_snbt_extractor import process_quest_folder
from ..utils.config_manager import load_config
from ..utils.text_processor import (
    convert_text,
    load_custom_translations,
    load_replace_rules,
    orjson_dump_file,
    orjson_pretty_str,
    recursive_translate,
)
from translation_tool.core.ftb_translator_clean import (
    deep_merge_3way as _deep_merge_3way_impl,
    clean_ftbquests_from_raw_impl,
    prune_en_us_by_zh_tw,
    prune_flat_en_by_tw,
)
from translation_tool.core.ftb_translator_export import (
    export_ftbquests_raw_json_impl,
    resolve_ftbquests_quests_root_impl,
)
from translation_tool.core.ftb_translator_template import (
    prepare_ftbquests_lang_template_only_impl,
)
from translation_tool.core.lm_translator_shared import _get_default_batch_size
from translation_tool.utils.log_unit import (
    get_formatted_duration,
    log_error,
    log_info,
    log_warning,
)


def _translate_single_file(
    file_path: str,
    input_dir: str,
    output_dir: str,
    rules: List[Dict[str, str]],
    custom_translations: Dict[str, str],
) -> str:
    """翻譯單一檔案並寫入輸出目錄。"""
    relative_path = os.path.relpath(file_path, input_dir)
    output_path = os.path.join(output_dir, relative_path).replace("zh_cn", "zh_tw")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    log_msg = f"正在翻譯: {relative_path}"
    log_info(log_msg)

    try:
        if file_path.endswith(".json"):
            with open(file_path, "rb") as f:
                data = orjson.loads(f.read())
            translated_data = recursive_translate(data, rules, custom_translations)
            with open(output_path, "wb") as f:
                orjson_dump_file(translated_data, f)

        elif file_path.endswith((".snbt", ".snbt.qkdownloading", ".js", ".md")):
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            translated_content = convert_text(content, rules)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(translated_content)

        return log_msg
    except Exception as e:
        log_error("處理檔案 %s 時發生錯誤: %s", relative_path, e)
        return f"處理 {relative_path} 失敗: {e}"


def translate_directory_generator(input_dir: str) -> Generator[Dict[str, Any], None, None]:
    """主翻譯流程，支援多執行緒處理。"""
    output_dir_name = (
        load_config().get("translator", {}).get("output_dir_name", "FTB任務翻譯輸出")
    )
    output_dir = os.path.join(os.path.dirname(input_dir), output_dir_name)
    os.makedirs(output_dir, exist_ok=True)

    log_info(f"FTB 翻譯開始 (多執行緒模式)，來源: {input_dir}")

    rules = load_replace_rules(
        load_config().get("translator", {}).get("replace_rules_path", "replace_rules.json")
    )
    custom_translations = load_custom_translations(
        load_config().get("translator", {}).get("custom_translator_folder", "custom_translators")
    )

    files_to_translate = []
    all_files_to_copy = []
    for root, _, files in os.walk(input_dir):
        for file in files:
            full_path = os.path.join(root, file)
            all_files_to_copy.append(full_path)
            if file.endswith((".json", ".snbt", ".snbt.qkdownloading", ".js", ".md")):
                files_to_translate.append(full_path)

    total_files = len(files_to_translate)
    if total_files == 0:
        log_warning("在指定目錄中沒有找到任何 .json、.snbt 或 .snbt.qkdownloading 檔案。")

    start_time = time.time()

    if total_files > 0:
        log_info(f"找到 {total_files} 個檔案，開始並行翻譯...")
        yield {"progress": 0.0}

        processed_count = 0
        max_workers = min(2, os.cpu_count() or 1)

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_file = {
                executor.submit(
                    _translate_single_file,
                    fp,
                    input_dir,
                    output_dir,
                    rules,
                    custom_translations,
                ): fp
                for fp in files_to_translate
            }

            for future in concurrent.futures.as_completed(future_to_file):
                processed_count += 1
                progress = processed_count / total_files
                log_msg = future.result()
                log_info(f"[{processed_count}/{total_files}] {log_msg}")
                yield {"progress": progress}

    log_info("翻譯階段完成，開始複製其餘檔案...")
    yield {"progress": 1.0}

    copied_count = 0
    for src_path in all_files_to_copy:
        if src_path not in files_to_translate:
            try:
                rel_path = os.path.relpath(src_path, input_dir)
                dst_path = os.path.join(output_dir, rel_path).replace("zh_cn", "zh_tw")
                os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                shutil.copy2(src_path, dst_path)
                copied_count += 1
            except Exception as e:
                log_error(f"複製檔案 {src_path} 失敗: {e}")

    log_info(f"複製階段完成，總共複製了 {copied_count} 個非翻譯檔案。")
    yield {"progress": 1.0}

    duration = time.time() - start_time
    final_log_msg = f"--- 所有 {total_files} 個檔案翻譯和 {copied_count} 個檔案複製完成，總耗時 {duration:.2f} 秒 ---"
    log_info(final_log_msg)
    yield {"progress": 1.0}


def deep_merge_3way(zh_tw: dict, zh_cn: dict, en_us: dict) -> dict:
    return _deep_merge_3way_impl(zh_tw, zh_cn, en_us)


def resolve_ftbquests_quests_root(base_dir: str) -> str:
    return resolve_ftbquests_quests_root_impl(base_dir)


def export_ftbquests_raw_json(base_dir: str, *, output_dir: str | None = None) -> dict:
    return export_ftbquests_raw_json_impl(
        base_dir,
        output_dir=output_dir,
        resolve_ftbquests_quests_root_fn=resolve_ftbquests_quests_root,
        process_quest_folder_fn=process_quest_folder,
        orjson_dump_file_fn=orjson_dump_file,
        log_info_fn=log_info,
    )


def clean_ftbquests_from_raw(base_dir: str, *, output_dir: str | None = None) -> dict:
    return clean_ftbquests_from_raw_impl(
        base_dir,
        output_dir=output_dir,
        orjson_loads=orjson.loads,
        orjson_dump_file_fn=orjson_dump_file,
        log_info_fn=log_info,
    )


def prepare_ftbquests_lang_template_only(
    input_config_dir: str,
    output_config_dir: str,
    *,
    prefer_lang: str = "zh_cn",
) -> dict:
    return prepare_ftbquests_lang_template_only_impl(
        input_config_dir,
        output_config_dir,
        prefer_lang=prefer_lang,
    )


# NOTE: FTB pipeline does NOT use translate_directory_generator

def run_ftb_pipeline(
    directory_path: str,
    session=None,
    output_dir: str | None = None,
    dry_run: bool = False,
    step_export: bool = True,
    step_clean: bool = True,
    step_translate: bool = True,
    step_inject: bool = True,
    write_new_cache: bool = True,
) -> dict:
    """FTB Quests 全流程 Pipeline（UI / services / CLI 都可用）。"""
    result: dict = {}

    if step_export:
        log_info("📦 [步驟 1/4] 開始抽取 FTB Quests 原始文本...")
        start_time = time.perf_counter()
        raw_paths = export_ftbquests_raw_json(directory_path, output_dir=output_dir)
        result["raw_paths"] = raw_paths
        log_info(f"✅ 原始資料抽取完成！\n  儲存位置: {raw_paths.get('raw_root')}")

    if step_clean:
        log_info("🧹 [步驟 2/4] 執行資料清洗與三語合併 (正在對比現有繁簡中文)...")
        clean_paths = clean_ftbquests_from_raw(directory_path, output_dir=output_dir)
        result["clean_paths"] = clean_paths
        log_info(
            "✅ 資料清洗完畢：\n"
            f"  1. 生成待翻譯檔 (en_us): {clean_paths.get('en_pending_dir')}\n"
            f"  2. 整合現有中文 (zh_tw): {clean_paths.get('zh_tw_dir')}"
        )

    if step_translate:
        clean_paths = result.get("clean_paths") if isinstance(result.get("clean_paths"), dict) else {}
        en_pending_dir = (clean_paths or {}).get("en_pending_dir")

        if not en_pending_dir:
            raise RuntimeError("Step3 需要 Step2 Clean 先產出 en_pending_dir（請先勾 Step2）")

        input_lang_dir = os.path.dirname(en_pending_dir)
        out_root = output_dir or os.path.join(directory_path, "Output")
        output_lang_dir = os.path.join(
            out_root, "ftbquests", "LM翻譯輸出", "config", "ftbquests", "quests", "lang"
        )

        log_info(f"🌐 [步驟 3/4] 啟動 Gemini AI 翻譯階段... (模擬模式: {'開啟' if dry_run else '關閉'})")

        from ..plugins.ftbquests.ftbquests_lmtranslator import translate_ftb_pending_to_zh_tw

        lm_res = translate_ftb_pending_to_zh_tw(
            input_lang_dir=input_lang_dir,
            output_lang_dir=output_lang_dir,
            session=session,
            dry_run=dry_run,
            write_new_cache=write_new_cache,
        )
        result["lm_translate"] = lm_res
        try:
            cache_miss = lm_res.get("cache_miss") if isinstance(lm_res, dict) else None
            batch_size = _get_default_batch_size("ftbquests", None)
            est_batches = math.ceil(cache_miss / batch_size) if isinstance(cache_miss, int) and batch_size > 0 else None
            if isinstance(lm_res, dict):
                lm_res = dict(lm_res)
                lm_res["estimated_batches"] = est_batches
        except Exception:
            pass

        log_info(f"✅ AI 翻譯階段結束。詳細統計：\n{orjson_pretty_str(lm_res)}")

    if dry_run:
        log_info("🧪 偵測到模擬模式 (Dry-run)：跳過步驟 4 Inject (不實際寫入檔案)。")
        result["inject"] = {"skipped": True, "reason": "dry_run"}
        return result

    if step_inject:
        try:
            from ..plugins.ftbquests.ftbquests_snbt_inject import (
                inject_ftbquests_quests_from_zh_tw_json,
                inject_ftbquests_zh_tw_from_jsons,
            )
        except Exception:
            raise

        out_root = output_dir or os.path.join(directory_path, "Output")
        zh_tw_dir = os.path.join(
            out_root,
            "ftbquests",
            "LM翻譯輸出",
            "config",
            "ftbquests",
            "quests",
            "lang",
            "zh_tw",
        )

        zh_tw_lang_json_path = os.path.join(zh_tw_dir, "ftb_lang.json")
        zh_tw_quests_json_path = os.path.join(zh_tw_dir, "ftb_quests.json")
        lang_exists = os.path.isfile(zh_tw_lang_json_path)
        quests_exists = os.path.isfile(zh_tw_quests_json_path)

        if not lang_exists and not quests_exists:
            try:
                existing_jsons = [f for f in os.listdir(zh_tw_dir) if f.lower().endswith(".json")] if os.path.isdir(zh_tw_dir) else []
            except Exception:
                existing_jsons = []

            raise FileNotFoundError(
                "找不到 Step3 產出的任何 zh_tw JSON：\n"
                f"- {zh_tw_lang_json_path}\n"
                f"- {zh_tw_quests_json_path}\n"
                + (f"\n（目前 zh_tw 目錄內的 json：{existing_jsons}）" if existing_jsons else "")
                + "\n（請先勾 Step3：Gemini 翻譯 pending/en_us → 輸出到 output_dir/config/.../lang/zh_tw/）"
            )

        log_info(
            "🧩 [步驟 4/4] 開始將翻譯結果注入 SNBT 檔案...\n"
            f"  - 介面文字 (ftb_lang)：{'✅ 找到' if lang_exists else '❌ 缺少'}（ftb_lang.json）\n"
            f"  - 任務內容 (ftb_quests)：{'✅ 找到' if quests_exists else '❌ 缺少'}（ftb_quests.json）"
        )

        input_config_dir = os.path.join(directory_path, "config")
        output_config_dir = os.path.join(output_dir, "ftbquests", "完成", "config")

        log_info(f"📁 Inject template from = {input_config_dir}")
        log_info(f"📁 Inject write to     = {output_config_dir}")
        result.setdefault("inject", {})

        if quests_exists:
            quests_inject_res = inject_ftbquests_quests_from_zh_tw_json(
                input_config_dir=input_config_dir,
                output_config_dir=output_config_dir,
                zh_tw_quests_json_path=zh_tw_quests_json_path,
            )
            result["inject"]["quests"] = quests_inject_res
            log_info(
                "✅ [任務注入] 任務本體 (.snbt) 處理完成\n"
                f"  輸出目錄：{quests_inject_res.get('output_quests_dir')}\n"
                f"  複製檔案：已準備 {quests_inject_res.get('template_files_copied')} 個任務模板檔\n"
                f"  修改檔案：已成功注入 {quests_inject_res.get('patched_files')} 個任務檔案\n"
                f"  翻譯條目：成功更新 {quests_inject_res.get('patched_keys_changed')} 條文本 "
                f"(總計可對應數: {quests_inject_res.get('patched_keys_candidates')})\n"
                f"  遺失檔案：有 {quests_inject_res.get('missing_source_files')} 個來源檔不存在"
            )

        if lang_exists:
            try:
                tpl_info = prepare_ftbquests_lang_template_only(
                    input_config_dir,
                    output_config_dir,
                    prefer_lang="zh_cn",
                )
                log_info(f"📄 Template prepared: {tpl_info}")

                lang_inject_res = inject_ftbquests_zh_tw_from_jsons(
                    output_config_dir,
                    zh_tw_lang_json_path,
                    None,
                    template_prefer="zh_cn",
                    overwrite_template_copy=True,
                )
                result.setdefault("inject", {})
                result["inject"]["lang"] = lang_inject_res
                log_info("✅ [FTB-INJECT-LANG] 完成")

            except FileNotFoundError as e:
                log_warning(
                    "⚠️ 找不到 FTB Quests lang 模板（zh_cn/en_us 的資料夾制/單檔制都沒有），已略過 lang 注入（quests 注入不受影響）。\n%s",
                    e,
                )
                result.setdefault("inject", {})
                result["inject"]["lang"] = {
                    "skipped": True,
                    "reason": "no_lang_template",
                }

        log_info("✅ Output lang dir = " + os.path.join(output_config_dir, "ftbquests", "quests", "lang"))

        duration = get_formatted_duration(start_time)
        quests_summary = result.get("inject", {}).get("quests", {}) or {}
        lang_summary = result.get("inject", {}).get("lang", {}) or {}
        patched_changed = int(quests_summary.get("patched_keys_changed") or 0)
        patched_candidates = int(quests_summary.get("patched_keys_candidates") or 0)
        missing_updated = max(patched_candidates - patched_changed, 0)
        coverage = (patched_changed / patched_candidates * 100.0) if patched_candidates > 0 else 0.0

        log_info(
            "🎉 --- FTB Quests 翻譯流程全部完成 --- \n"
            f" 📁 最終輸出路徑: {output_config_dir}\n"
            f" 📄 任務修改總數: {quests_summary.get('patched_files')} 個檔案\n"
            f" 🔑 翻譯覆蓋：已更新 {patched_changed} / 可翻譯 {patched_candidates}（{coverage:.1f}%），未更新 {missing_updated}\n"
            f" ⚠️ 缺失源檔數: {quests_summary.get('missing_source_files')}\n"
            f" 🏷️ 語言模板注入: {'已跳過' if lang_summary.get('skipped') else '執行成功' if lang_summary else '無資料'}\n"
            f" ⏱️ 總共執行耗時: {duration}\n"
            "--------------------------------------"
        )

    return result


__all__ = [
    "translate_directory_generator",
    "deep_merge_3way",
    "prune_en_us_by_zh_tw",
    "prune_flat_en_by_tw",
    "resolve_ftbquests_quests_root",
    "export_ftbquests_raw_json",
    "clean_ftbquests_from_raw",
    "prepare_ftbquests_lang_template_only",
    "run_ftb_pipeline",
]
