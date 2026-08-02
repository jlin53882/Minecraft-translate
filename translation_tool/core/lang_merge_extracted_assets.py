"""merge_extracted_assets 工具,解析 README。

目的 (2026-08-02):
    lang_merger.py 的階段 1 (zh_cn → zh_tw 翻譯) 處理完後,
    input_dir 內 XX_extracted/{modid}/lang/{xx_yy}.json 並未推到 minecraft 認得的
    `assets/{modid}/lang/{xx_yy}.json`。本 module 負責這個局勢:
    掃描 output_dir/lang_output/{XX_extracted,...}/ 內所有 lang 檔,
    key-by-key 合併進 output_dir/lang_output/assets/{modid}/lang/{xx_yy}.json。

設計:
    - 抽共用 SRP:階段 1 只翻譯,階段 2 只合併。
    - user-asset priority 一律 (assets wins):只在 key 不存在時加进去。
    - 多 source 同 modid 同 lang_code:第 1 個 wins + log warning。
    - yield `{"log", "progress"}` 與 lang_merger 的協定對齊,pipeline UI 直接讀。
    - 失败 log 不污染階段 1 結果 (語言翻譯已完成在硬碟)。
"""
from __future__ import annotations

import json
import shutil  # noqa: F401  shutil 用於 _cleanup_extracted_dirs 刪除整個 _extracted 子資料夾
import os
import re
import traceback
from collections import defaultdict
from pathlib import Path
from typing import Any, Generator

from translation_tool.utils.log_unit import log_info, log_warning
from translation_tool.utils.safe_json_loader import load_json_auto_encoding


# 任何 *_extracted 結尾 (含可選版本後綴) 算會被這個 pattern 挑到
# re.match(r".*_extracted(_\w+)?$", name) 接受:
#   ae2ct_extracted
#   ae2ct_extracted_v2
#   Cobblemon-1.7.3+1.21.1_extracted
#   _cache_ae2ct_extracted
# 拒絕:
#   random
#   extracted (沒前綴)
_EXTRACTED_NAME_RE = re.compile(r".*_extracted(_\w+)?$")
_LANG_FILE_GLOB = "**/lang/*.json"
_LANG_CODE_STEM = re.compile(r"^[A-Za-z_]+$")  # en_us, zh_cn, zh_tw, ru_ru ...


def _infer_modid_from_lang_file(lang_file: Path) -> str | None:
    """從 lang_file 路徑推導出 modid。
    
    規則:
      1. 若路徑含 assets/{modid}/lang/ → modid = {modid}。
      2. 否则 取 lang/ parent 的名稱 ── 包含兜底情形「{modid}/lang/」。
    
    Returns:
        推導出的 modid,path 推不出來則 None。
    """
    path_parts = lang_file.parts
    # 找 'lang' 這個 part 的 index
    try:
        lang_idx = len(path_parts) - 1 - path_parts[::-1].index("lang")
    except ValueError:
        return None
    
    if lang_idx < 1:
        return None
    
    modid_dir = path_parts[lang_idx - 1]  # .../{modid}/lang
    # 若是 .../assets/{modid}/lang/ → 用 {modid} (其實都同)
    return modid_dir


def _scan_extracted_lang_files(lang_output_dir: Path) -> dict[str, dict[str, list[Path]]]:
    """掃描 lang_output_dir 內 XX_extracted 子資料夾的 lang 檔案。

    退出 assets/ 子資料夾 (存為是階段 2 目標,不是來源)。

    Returns:
        {modid: {lang_code: [source_paths]}}
        `assets` 不會是 modid (它是被寫目標)。
    """
    result: dict[str, dict[str, list[Path]]] = defaultdict(lambda: defaultdict(list))

    if not lang_output_dir.exists():
        return dict(result)

    scanned_dirs = []  # 2026-08-02 user 確認:加 log 確認掃到哪些 _extracted
    skipped_dirs = []
    for entry in lang_output_dir.iterdir():
        if not entry.is_dir():
            continue
        # 跳過 assets/ (目標資料夾)與 待翻譯整理/待翻譯 (階段 1 已寫的東西)
        if entry.name in ("assets", "待翻譯整理", "待翻譯"):
            continue
        # 只處理符合 *_extracted 結尾 (含可選版本後綴) 的子資料夾
        if not _EXTRACTED_NAME_RE.match(entry.name):
            continue

        file_count = sum(1 for _ in entry.rglob(_LANG_FILE_GLOB) if _.is_file())
        if file_count == 0:
            # 2026-08-02:log 哪些 _extracted 沒找到 lang 檔案(為什麼沒處理)
            log_warning(
                f"[MergeExt→Assets] {entry.name}/* 內找不到 lang/*.json,"
                f" 跳過(階段1可能把 en_us-only 檔案搬到 待翻譯/)"
            )
            skipped_dirs.append(entry.name)
            continue

        scanned_dirs.append(entry.name)
        for lang_file in entry.rglob(_LANG_FILE_GLOB):
            if not lang_file.is_file():
                continue
            modid = _infer_modid_from_lang_file(lang_file)
            if not modid:
                log_warning(
                    f"[MergeExt→Assets] 推不出 modid: {lang_file}, 跳過"
                )
                continue
            lang_code = lang_file.stem
            if not _LANG_CODE_STEM.match(lang_code):
                log_warning(
                    f"[MergeExt→Assets] 推不出 lang_code: {lang_file}, 跳過"
                )
                continue
            result[modid][lang_code].append(lang_file)

    # 2026-08-02:加彙總 log 讓 user 知道實際掃到的 _extracted dirs
    if scanned_dirs:
        log_info(
            f"[MergeExt→Assets] 掃描 _extracted dirs: {len(scanned_dirs)} 個"
            f" ({', '.join(scanned_dirs)})"
        )
    if skipped_dirs:
        log_info(
            f"[MergeExt→Assets] 跳過 (沒找到 lang/*.json): {len(skipped_dirs)} 個"
            f" ({', '.join(skipped_dirs)})"
        )

    return dict(result)


def _load_existing_assets(assets_dir: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """讀取 assets/{modid}/lang/{xx_yy}.json 全部現有資料。
    
    Returns:
        {(modid, lang_code): {key: value}}
    """
    result: dict[tuple[str, str], dict[str, Any]] = {}
    if not assets_dir.exists():
        return result
    
    for modid_dir in assets_dir.iterdir():
        if not modid_dir.is_dir():
            continue
        modid = modid_dir.name
        lang_dir = modid_dir / "lang"
        if not lang_dir.exists() or not lang_dir.is_dir():
            continue
        for lang_file in lang_dir.iterdir():
            if lang_file.suffix.lower() != ".json":
                continue
            lang_code = lang_file.stem
            data = load_json_auto_encoding(lang_file)
            if data is None:
                data = {}
            result[(modid, lang_code)] = dict(data)
    return result


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    """原子寫入 JSON dict 到 path。
    
    使用 UTF-8 + ensure_ascii=False + indent=4 + \n 換行符號,
    跟階段 1 lang_merger 的 dump_json_bytes 對齊。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        f.write("\n")
    os.replace(tmp_path, path)


def _cleanup_extracted_dirs(lang_output_dir: Path, session: Any = None) -> int:
    """Stage 2 完成後,刪除 lang_output_dir 下所有 *_extracted 子資料夾。

    目的 (2026-08-02 user 確認):
        防止下次 merge 時,_scan_extracted_lang_files 又掃到這些已合併的 ext 目錄,
        造成重複處理或不預期的 side effect。

    Args:
        lang_output_dir: 通常是 `{output_dir}/lang_output`。
        session: 任意有 add_log() 介面的物件 (可選)。

    Returns:
        刪除的子資料夾數量。
    """
    if not lang_output_dir.exists():
        return 0

    cleaned = 0
    for entry in list(lang_output_dir.iterdir()):
        if not entry.is_dir():
            continue
        if not _EXTRACTED_NAME_RE.match(entry.name):
            continue
        try:
            shutil.rmtree(entry)
            cleaned += 1
            log_info(f"[MergeExt→Assets] 已清理 _extracted 子資料夾: {entry.name}")
            if session is not None:
                try:
                    session.add_log(
                        f"[清理] 已刪除 {entry.name}/ (內容已併入 assets/)"
                    )
                except Exception:
                    pass
        except Exception as exc:
            log_warning(
                f"[MergeExt→Assets] 無法刪除 {entry}: {exc!r}"
            )
    return cleaned


def merge_extracted_to_assets(
    lang_output_dir: str | Path,
    session: Any = None,
) -> Generator[dict[str, Any], None, None]:
    """合併階段 2:把 lang_output_dir 內 XX_extracted 的 lang 檔 key-by-key 進 assets/。

    Args:
        lang_output_dir: 通常是 `{output_dir}/lang_output`。
            函式會同時讀它下面的 XX_extracted/*/lang/*.json
            以及下面的 assets/{modid}/lang/*.json (既有最終結果)。
        session: 任意的有 add_log()/set_progress() 介面的物件 (可選)。

    Yields:
        {"progress": float, "log": str | None, "error": bool}
        pipeline UI poller 直接讀。

    進度計算 (2026-08-02):
        階段 1 結束時 progress 通常已達 1.0 (折疊去看 session.progress)。
        階段 2 接手 (1.0 → 1.0) 但保留了空間顯示階段 2 進度。
        變更:階段 2 進度鏡像 50%-100% 讓 UI 進度條不跳 (Fake)。
        真實下 session.progress 會是 0.8 / 1.0 這範圍。
    """
    lang_output_dir = Path(lang_output_dir)
    assets_dir = lang_output_dir / "assets"
    
    log_info(f"[MergeExt→Assets] 開始,掃描 {lang_output_dir}")
    if session is not None:
        try:
            session.add_log("[MergeExt→Assets] 開始掃描 XX_extracted/")
        except Exception:
            pass
    
    if not lang_output_dir.exists():
        log_warning(f"[MergeExt→Assets] 不存在: {lang_output_dir}")
        yield {"progress": 1.0, "log": None, "error": False}
        return
    
    try:
        existing = _load_existing_assets(assets_dir)
        extracted = _scan_extracted_lang_files(lang_output_dir)
        
        if not extracted:
            log_info("[MergeExt→Assets] 沒找到 XX_extracted/*, 跳過 (無源可合併)")
            yield {"progress": 1.0, "log": None, "error": False}
            return
        
        total_modids = len(extracted)
        total_added = 0
        total_files_written = 0
        total_warnings = 0
        seen_pairs: set[tuple[str, str]] = set()

        # 進度範圍:session.progress (階段 1 完成時已 1.0) → 1.0
        # 但我們要給階段 2 留 mirror 2.5% 空間,讓 UI 看到階段 2 在跑
        # 公式: base_progress + (idx / total_modids) * (1.0 - base_progress)
        if session is not None and hasattr(session, "snapshot"):
            # session.progress 通常 1.0 (階段 1 已經填滿)
            base_progress = max(0.0, min(0.999, session.progress))
        else:
            base_progress = 0.0
        span = 1.0 - base_progress
        
        for idx, (modid, lang_files) in enumerate(extracted.items(), start=1):
            mod_added_count = 0
            for lang_code, source_paths in lang_files.items():
                target_key = (modid, lang_code)
                target_path = assets_dir / modid / "lang" / f"{lang_code}.json"
                
                # target_data = 既有 assets (若有)
                target_data = dict(existing.get(target_key, {}))
                before_keys = set(target_data.keys())
                
                # 多 source:第 1 個 wins,後面撞到同 key log warning
                for source_idx, source_path in enumerate(source_paths):
                    source_data = load_json_auto_encoding(source_path)
                    if source_data is None:
                        source_data = {}
                    if not isinstance(source_data, dict):
                        log_warning(
                            f"[MergeExt→Assets] {source_path} 不是 dict 格式, 跳過"
                        )
                        total_warnings += 1
                        continue
                    
                    for key, val in source_data.items():
                        if key in target_data:
                            # assets wins:跳過不覆寫
                            continue
                        # assets wins 對應:查 XX_extracted 是不是也有同名 key
                        if key not in target_data:
                            target_data[key] = val
                            mod_added_count += 1
                
                # 寫回 (whenever 有變動)
                added_this_file = len(target_data) - len(before_keys)
                if added_this_file > 0:
                    try:
                        _write_json_atomic(target_path, target_data)
                        total_files_written += 1
                        if session is not None:
                            try:
                                session.add_log(
                                    f"  ✓ {modid}/{lang_code}.json: +{added_this_file} 個 key"
                                )
                            except Exception:
                                pass
                    except Exception as exc:
                        log_warning(
                            f"[MergeExt→Assets] 寫入失敗 {target_path}: {exc}"
                        )
                        total_warnings += 1
                
                existing[target_key] = target_data
                seen_pairs.add(target_key)
                
                # multi source warning
                if len(source_paths) > 1:
                    log_warning(
                        f"[MergeExt→Assets] {modid}/{lang_code}: 多個來源 {len(source_paths)} 個,"
                        f" assets wins 只補缺。"
                    )
            
            total_added += mod_added_count
            if session is not None:
                try:
                    session.add_log(f"  ✓ {modid}: +{mod_added_count} 個 key")
                except Exception:
                    pass
            
            progress = base_progress + (idx / total_modids) * span
            yield {
                "progress": progress,
                "log": None,
                "error": False,
            }
        
        log_info(
            f"[MergeExt→Assets] 完成: {total_modids} 個 modid,"
            f" {total_added} 個 key 並入,"
            f" {total_files_written} 檔寫入,"
            f" {total_warnings} 個 warning"
        )
        if session is not None:
            try:
                session.add_log(
                    f"[MergeExt→Assets] 完成: {total_added} 個 key 已並入 assets/"
                )
            except Exception:
                pass
        # 2026-08-02 user 確認:
        #   Stage 2 完成後刪除 *_extracted 子資料夾,
        #   防止下次 merge 時 _scan 又掃到這些已經合併的目錄。
        try:
            cleaned = _cleanup_extracted_dirs(lang_output_dir, session=session)
            if cleaned > 0:
                log_info(
                    f"[MergeExt→Assets] 清理完成:刪除 {cleaned} 個 _extracted 子資料夾"
                )
        except Exception as exc:
            log_warning(f"[MergeExt→Assets] 清理 _extracted 失敗: {exc!r}")
        yield {"progress": 1.0, "log": None, "error": False}
    
    except Exception as exc:
        tb = traceback.format_exc()
        log_warning(f"[MergeExt→Assets] 錯誤: {exc}\n{tb}")
        if session is not None:
            try:
                session.add_log(f"[MergeExt→Assets] 錯誤: {exc}")
            except Exception:
                pass
        yield {"progress": 1.0, "log": None, "error": True}
