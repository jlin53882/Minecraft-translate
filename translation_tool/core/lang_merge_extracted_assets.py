"""merge_extracted_assets 工具,解析 README。

目的 (2026-08-02):
    lang_merger.py 的階段 1 (zh_cn → zh_tw 翻譯) 處理完後,
    input_dir 內 XX_extracted/{modid}/lang/{xx_yy}.json 並未推到 minecraft 認得的
    `assets/{modid}/lang/{xx_yy}.json`。本 module 負責這個局勢:
    掃描 output_dir/lang_output/{XX_extracted,...}/ 內所有 lang 檔,
    用 Stage 1 的 merge_lang_dicts 邏輯產出 zh_tw.json,
    寫到 output_dir/lang_output/assets/{modid}/lang/zh_tw.json。

設計:
    - 抽共用 SRP:階段 1 只翻譯,階段 2 只搬 zh_tw 結果到 assets
    - user-asset priority 一律 (assets wins):zh_tw 用 merge_lang_dicts 的規則,
      人工翻譯的 zh_tw 不被覆寫
    - 失敗隔離:階段 2 錯誤不中断階段 1 結果 (語言翻譯已完成在硬碟)
    - 寬掃 _extracted/ 跟 待翻譯/ 兩個位置:en_us-only mod 的檔案
      會被 Stage 1 搬到 待翻譯/,寬掃確保全部 modid 都進 assets
"""

from __future__ import annotations

import json
import os
import re
import shutil  # 用於 _cleanup_extracted_dirs 刪除整個 _extracted 子資料夾
import traceback
from collections import defaultdict
from collections.abc import Generator, Iterable
from pathlib import Path
from typing import Any

from translation_tool.core.lang_codec import dump_lang_text, parse_lang_text
from translation_tool.core.lang_merge_db import merge_db_fill
from translation_tool.core.lang_merge_dict import (
    contains_cjk as stage2_contains_cjk,
)
from translation_tool.core.lang_merge_dict import (
    is_pure_english as stage2_is_pure_english,
)
from translation_tool.core.lang_merge_dict import (
    merge_lang_dicts,
)
from translation_tool.utils.log_unit import log_debug, log_info, log_warning
from translation_tool.utils.safe_json_loader import load_json_auto_encoding
from translation_tool.utils.text_processor import (
    apply_replace_rules,
    recursive_translate_dict,
)

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


def _scan_extracted_lang_files(
    lang_output_dir: Path,
) -> dict[str, dict[str, list[Path]]]:
    """掃描 lang_output_dir 內 XX_extracted 子資料夾的 lang 檔案。

    退出 assets/ 子資料夾 (存為是階段 2 目標,不是來源)。

    2026-08-02 user 修正:
        寬掃兩個位置:
        1. lang_output_dir/{XX_extracted}/{modid}/lang/*.json
           - zh_tw.json 從 stage 1 寫的 (zh_cn → zh_tw 翻譯完成)
        2. lang_output_dir/待翻譯/{XX_extracted}/{modid}/lang/*.json
           - en_us.json 從 stage 1 寫的 (en_us-only mod 的原文)
        因為 stage 1 將 en_us-only mod 的 en_us 搬到 待翻譯/,而不是 _extracted/。

    Returns:
        {modid: {lang_code: [source_paths]}}
        `assets` 不會是 modid (它是被寫目標)。
    """
    result: dict[str, dict[str, list[Path]]] = defaultdict(lambda: defaultdict(list))

    if not lang_output_dir.exists():
        return dict(result)

    # 2026-08-02 user 確認:寬掃兩個位置
    scan_roots = [lang_output_dir, lang_output_dir / "待翻譯"]
    scanned_dirs: list[str] = []  # 2026-08-02 user 確認:加 log 確認掃到哪些 _extracted
    skipped_dirs = []

    for scan_root in scan_roots:
        if not scan_root.exists():
            continue
        for entry in scan_root.iterdir():
            if not entry.is_dir():
                continue
            # 跳過 assets/ (目標資料夾)與 待翻譯整理/待翻譯 (待翻譯 已在 scan_root handle)
            if entry.name in ("assets", "待翻譯整理", "待翻譯"):
                continue
            # 只處理符合 *_extracted 結尾 (含可選版本後綴) 的子資料夾
            if not _EXTRACTED_NAME_RE.match(entry.name):
                continue

            file_count = sum(1 for _ in entry.rglob(_LANG_FILE_GLOB) if _.is_file())
            if file_count == 0:
                # 2026-08-02:log 哪些 _extracted 沒找到 lang 檔案(為什麼沒處理)
                log_warning(
                    f"[MergeExt→Assets] {entry.name}/* 內找不到 lang/*.json, 跳過"
                )
                skipped_dirs.append(entry.name)
                continue

            scanned_dirs.append(entry.name)
            for lang_file in entry.rglob(_LANG_FILE_GLOB):
                if not lang_file.is_file():
                    continue
                modid = _infer_modid_from_lang_file(lang_file)
                if not modid:
                    log_warning(f"[MergeExt→Assets] 推不出 modid: {lang_file}, 跳過")
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


class _LazyExistingAssets:
    """2026-08-04 B1: lazy load assets/{modid}/lang/{xx_yy}.json on-demand。

    避免 Stage 2 開始時就把幾百個 mod 的 assets 全載入記憶體。
    """

    def __init__(self, assets_dir: Path):
        self._assets_dir = assets_dir
        self._cache: dict[tuple[str, str], dict[str, Any]] = {}

    def get(self, key: tuple[str, str], default=None) -> dict[str, Any]:
        if key not in self._cache:
            modid, lang_code = key
            lang_file = self._assets_dir / modid / "lang" / f"{lang_code}.json"
            if lang_file.exists():
                data = load_json_auto_encoding(lang_file)
                self._cache[key] = dict(data) if data else {}
            else:
                self._cache[key] = {}
        return self._cache[key]

    def __contains__(self, key):
        modid, lang_code = key
        return (self._assets_dir / modid / "lang" / f"{lang_code}.json").exists()

    def __len__(self):
        return len(self._cache)


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
    try:
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
            f.write("\n")
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError as cleanup_error:
            log_warning(
                f"[MergeExt→Assets] 無法清理暫存檔 {tmp_path}: {cleanup_error!r}"
            )


def _cleanup_single_mod_extracted(
    lang_output_dir: Path,
    modid: str,
    source_paths: Iterable[Path] | None = None,
) -> bool:
    """2026-08-04 B3: 只刪除指定 modid 的 _extracted 目錄 (per-mod commit)。

    只有該 mod 寫入成功後才呼叫,避免寫失敗時 source 已被刪。
    """
    # Prefer the exact files discovered by the scanner.  Extracted JAR layouts
    # are not necessarily ``*_extracted/<modid>/...``; they can contain
    # wrapper directories such as ``packs/foo/assets/<modid>/...``.
    if source_paths is not None:
        cleaned = False
        for source_path in source_paths:
            source_path = Path(source_path)
            if not source_path.is_file():
                continue
            try:
                # A lang file always lives below ``<modid>/lang``.  Removing
                # that mod directory preserves sibling mods in the same
                # extracted container and keeps the cleanup atomic: if the
                # directory removal fails, the source remains available for a
                # retry.
                mod_dir = source_path.parent.parent
                if mod_dir.name != modid:
                    source_path.unlink()
                    mod_dir = source_path.parent
                else:
                    shutil.rmtree(mod_dir)
                cleaned = True
                current = mod_dir.parent
                extracted_root = next(
                    (
                        parent
                        for parent in (current, *current.parents)
                        if _EXTRACTED_NAME_RE.match(parent.name)
                    ),
                    None,
                )
                while current != lang_output_dir and current.exists():
                    if extracted_root is not None and current == extracted_root:
                        if any(current.iterdir()):
                            break
                        current.rmdir()
                        break
                    if any(current.iterdir()):
                        break
                    parent = current.parent
                    current.rmdir()
                    current = parent
            except OSError as exc:
                log_warning(f"[MergeExt→Assets] cleanup 失敗 ({modid}): {exc!r}")
        return cleaned

    # Backward-compatible fallback for callers/tests that only provide modid.
    for entry in lang_output_dir.iterdir():
        if not entry.is_dir():
            continue
        if not _EXTRACTED_NAME_RE.match(entry.name):
            continue
        mod_dir = entry / modid
        if mod_dir.exists() and mod_dir.is_dir():
            try:
                shutil.rmtree(mod_dir)
                if not any(entry.iterdir()):
                    shutil.rmtree(entry)
                return True
            except Exception as e:  # noqa: BLE001 - 失敗已記錄，不中斷批次流程
                # 2026-08-04: log warning instead of silent pass
                log_warning(f"[MergeExt→Assets] cleanup 失敗 ({modid}): {e!r}")
    return False


def _safe_session_log(session, message: str) -> None:
    """寫入 UI session 日誌；UI 日誌失敗不可中斷合併（只留 debug 紀錄）。"""
    try:
        session.add_log(message)
    except Exception:  # noqa: BLE001
        log_debug("[MergeExt→Assets] session.add_log 失敗", exc_info=True)


def normalize_pending_extracted_wrappers(
    lang_output_dir: str | Path,
    folder_names: Iterable[str] = ("待翻譯", "待翻譯整理需翻譯"),
) -> int:
    """把 pending 語言檔正規化成 ``assets/<modid>/lang`` 結構。

    pending 內容本身必須保留給後續翻譯；這裡只把
    ``待翻譯/**/lang/<lang>`` 搬成
    ``待翻譯/assets/<modid>/lang/<lang>``，避免 ``data``、``packs``、
    ``*_extracted`` 等暫存或 JAR 包裝路徑變成最終輸出結構的一部分。
    階段 2 掃描完成後才可呼叫此函式。
    """
    lang_output_dir = Path(lang_output_dir)
    moved = 0

    for folder_name in folder_names:
        root = lang_output_dir / folder_name
        if not root.is_dir():
            continue
        lang_files = [
            path
            for path in root.rglob("*")
            if path.is_file()
            and path.parent.name == "lang"
            and path.suffix.lower() in {".json", ".lang"}
        ]
        for source in lang_files:
            modid = _infer_modid_from_lang_file(source)
            if not modid:
                log_warning(
                    f"[MergeExt→Assets] pending 推不出 modid，保留來源: {source}"
                )
                continue
            destination = root / "assets" / modid / "lang" / source.name
            if source == destination:
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                try:
                    if source.suffix.lower() == ".json":
                        source_data = load_json_auto_encoding(source) or {}
                        destination_data = load_json_auto_encoding(destination) or {}
                        if not isinstance(source_data, dict) or not isinstance(
                            destination_data, dict
                        ):
                            raise ValueError("pending JSON 不是 object")
                        added = {
                            key: value
                            for key, value in source_data.items()
                            if key not in destination_data
                        }
                        if added:
                            _write_json_atomic(
                                destination,
                                {**destination_data, **added},
                            )
                    else:
                        source_data = parse_lang_text(
                            source.read_text(encoding="utf-8-sig")
                        )
                        destination_data = parse_lang_text(
                            destination.read_text(encoding="utf-8-sig")
                        )
                        added = {
                            key: value
                            for key, value in source_data.items()
                            if key not in destination_data
                        }
                        if added:
                            destination.write_text(
                                dump_lang_text({**destination_data, **added}),
                                encoding="utf-8",
                            )
                    source.unlink()
                    moved += 1
                except (OSError, TypeError, ValueError) as exc:
                    log_warning(
                        f"[MergeExt→Assets] pending 合併失敗，保留來源: {source}: {exc!r}"
                    )
                continue
            shutil.move(str(source), str(destination))
            moved += 1

        # 只移除搬移後剩下的空包裝目錄；任何未處理的非語言檔仍會保留。
        for directory in sorted(
            (path for path in root.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            try:
                directory.rmdir()
            except OSError:
                pass
    return moved


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
                _safe_session_log(
                    session, f"[清理] 已刪除 {entry.name}/ (內容已併入 assets/)"
                )
        except Exception as exc:  # noqa: BLE001 - 失敗已記錄，不中斷批次流程
            log_warning(f"[MergeExt→Assets] 無法刪除 {entry}: {exc!r}")
    return cleaned


def merge_extracted_to_assets(
    lang_output_dir: str | Path,
    session: Any = None,
    pending_folder_names: Iterable[str] | None = None,
    use_translation_db: bool | None = None,
    translation_db_version: str | None = None,
) -> Generator[dict[str, Any], None, None]:
    """合併階段 2（純英文條目會先向 Mod 資料庫補譯；其餘見 ``_merge_extracted_to_assets``）。"""
    with merge_db_fill(use_translation_db, translation_db_version) as db_fill:
        yield from _merge_extracted_to_assets(
            lang_output_dir, session, pending_folder_names, db_fill=db_fill
        )


def _merge_extracted_to_assets(
    lang_output_dir: str | Path,
    session: Any = None,
    pending_folder_names: Iterable[str] | None = None,
    db_fill: Any = None,
) -> Generator[dict[str, Any], None, None]:
    """合併階段 2:把 lang_output_dir 內 XX_extracted 的 lang 檔 key-by-key 進 assets/。

    Args:
        lang_output_dir: 通常是 `{output_dir}/lang_output`。
            函式會同時讀它下面的 XX_extracted/*/lang/*.json
            以及下面的 assets/{modid}/lang/*.json (既有最終結果)。
        session: 任意的有 add_log()/set_progress() 介面的物件 (可選)。
        pending_folder_names: 待翻譯與整理待翻譯資料夾名稱；未提供時使用預設名稱。

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
        _safe_session_log(session, "[MergeExt→Assets] 開始掃描 XX_extracted/")

    if not lang_output_dir.exists():
        log_warning(f"[MergeExt→Assets] 不存在: {lang_output_dir}")
        yield {"progress": 1.0, "log": None, "error": False}
        return

    try:
        # 2026-08-04 B1: lazy load 取代全量 _load_existing_assets
        existing = _LazyExistingAssets(assets_dir)
        extracted = _scan_extracted_lang_files(lang_output_dir)

        if not extracted:
            log_info("[MergeExt→Assets] 沒找到 XX_extracted/*, 跳過 (無源可合併)")
            normalize_pending_extracted_wrappers(
                lang_output_dir,
                folder_names=pending_folder_names or ("待翻譯", "待翻譯整理需翻譯"),
            )
            yield {"progress": 1.0, "log": None, "error": False}
            return

        total_modids = len(extracted)
        total_added = 0
        total_files_written = 0
        total_warnings = 0
        had_errors = False
        error_details: list[str] = []

        for idx, (modid, lang_files) in enumerate(extracted.items(), start=1):
            # 2026-08-02 重構:Stage 2 不再走 self-written key-by-key merge,
            # 改用 Stage 1 拆出來的 merge_lang_dicts helper (reused),
            # 但 output 寫 assets/{modid}/lang/zh_tw.json (跟 Stage 1 不同)。

            # 收集 3 個來源 (zh_cn, zh_tw, en_us) 從寬掃結果
            cn_data: dict = {}
            tw_src_data: dict = {}
            en_data: dict = {}
            mod_error = None

            # 多 source 時,以第一個 source 為主(同 Stage 1 _process_single_mod 行為)
            for lang_code, source_paths in lang_files.items():
                # 取第一個 source (Stage 1 _process_single_mod 也只看 1 個 lang per file)
                if not source_paths:
                    continue
                source_path = source_paths[0]
                if len(source_paths) > 1:
                    log_warning(
                        f"[MergeExt→Assets] {modid}/{lang_code}: 多個來源 {len(source_paths)} 個,"
                        f" 採第一個 ({source_path.name})"
                    )
                try:
                    data = load_json_auto_encoding(source_path)
                    if data is None or not isinstance(data, dict):
                        raise ValueError("來源 JSON 無法讀取或不是 dict 格式")
                except Exception as exc:  # noqa: BLE001 - 失敗已記錄，不中斷批次流程
                    mod_error = f"{modid}: read failed ({source_path}): {exc}"
                    log_warning(f"[MergeExt→Assets] {mod_error}")
                    total_warnings += 1
                    break
                if lang_code == "zh_cn":
                    cn_data = data
                elif lang_code == "zh_tw":
                    tw_src_data = data
                elif lang_code == "en_us":
                    en_data = data

            if mod_error:
                had_errors = True
                error_details.append(mod_error)
                continue

            # 跑 Stage 1 拆出來的 merge 邏輯 - 行為 1:1 一致
            try:
                # 既有 assets/{modid}/lang/zh_tw.json (人工翻譯保護)
                existing_tw = existing.get((modid, "zh_tw"), {})
            except Exception as exc:  # noqa: BLE001 - 失敗已記錄，不中斷批次流程
                mod_error = f"{modid}: read failed (existing assets): {exc}"
                log_warning(f"[MergeExt→Assets] {mod_error}")
                total_warnings += 1
                had_errors = True
                error_details.append(mod_error)
                continue

            try:
                final_tw, pending = merge_lang_dicts(
                    cn_data=cn_data,
                    tw_src_data=tw_src_data,
                    en_data=en_data,
                    existing_tw=existing_tw,
                    rules=[],  # Stage 2 不套 replace rules,這些是 Stage 1 翻譯階段用
                    apply_replace_rules=apply_replace_rules,
                    recursive_translate_dict=recursive_translate_dict,
                    contains_cjk=stage2_contains_cjk,
                    is_pure_english=stage2_is_pure_english,
                    is_from_output_dir=bool(existing_tw),
                )
                if db_fill is not None:
                    final_tw, pending, _db_hits = db_fill.fill(modid, final_tw, pending)
            except Exception as exc:  # noqa: BLE001 - 失敗已記錄，不中斷批次流程
                mod_error = f"{modid}: merge failed: {exc}"
                log_warning(f"[MergeExt→Assets] {mod_error}")
                total_warnings += 1
                had_errors = True
                error_details.append(mod_error)
                continue

            # 寫 assets/{modid}/lang/zh_tw.json (翻譯完成的結果)
            target_path = assets_dir / modid / "lang" / "zh_tw.json"
            mod_added_count = 0
            try:
                # 只寫有內容的 zh_tw.json (沒資料的 mod 不寫空檔案)
                if final_tw:
                    _write_json_atomic(target_path, final_tw)
                    total_files_written += 1
                    # 2026-08-04 B3: 寫成功才刪該 mod 的 _extracted source
                    # (per-mod commit).  Use the exact scanned source paths so
                    # deep wrapper layouts are cleaned as well.
                    try:
                        scanned_sources = [
                            paths[0]
                            for paths in lang_files.values()
                            if paths and "待翻譯" not in paths[0].parts
                        ]
                        _cleanup_single_mod_extracted(
                            lang_output_dir, modid, scanned_sources
                        )
                    except Exception as exc:  # noqa: BLE001 - 失敗已記錄，不中斷批次流程
                        log_warning(
                            f"[MergeExt→Assets] cleanup 失敗 ({modid}): {exc!r}"
                        )
                    mod_added_count = len(set(final_tw) - set(existing_tw))
                    if mod_added_count > 0:
                        total_added += mod_added_count
                    if session is not None:
                        _safe_session_log(
                            session,
                            f"  ✓ {modid}/zh_tw.json: {len(final_tw)} keys"
                            + (
                                f" (+{mod_added_count} 新)"
                                if mod_added_count > 0
                                else ""
                            ),
                        )
                        if mod_added_count == 0:
                            _safe_session_log(
                                session,
                                f"  - {modid}: 相同 key 已存在於 assets，略過新增",
                            )
                else:
                    # 沒 zh_tw 內容,不寫空檔案(避免污染 assets/)
                    if session is not None:
                        _safe_session_log(
                            session, f"  - {modid}: 沒 zh_tw 內容,跳過寫 assets/"
                        )
            except Exception as exc:  # noqa: BLE001 - 失敗已記錄，不中斷批次流程
                mod_error = f"{modid}: write failed ({target_path}): {exc}"
                log_warning(f"[MergeExt→Assets] {mod_error}")
                total_warnings += 1
                had_errors = True
                error_details.append(mod_error)
                progress = idx / total_modids
                yield {"progress": progress, "log": None, "error": False}
                continue

            # 2026-08-02 修正: pending 不寫到 assets/{modid}/lang/en_us.json
            # 來源已在 待翻譯/{XX_extracted}/{modid}/lang/en_us.json,
            # Stage 2 不重複寫到 assets/(會污染 minecraft 認得的位置)。
            # LM 翻譯完成後,user 再跑一次 merge,Stage 1 會讀 zh_tw.json
            # 並寫到 assets/{modid}/lang/zh_tw.json。
            if pending and session is not None:
                _safe_session_log(
                    session, f"  → {modid}: {len(pending)} pending 在 待翻譯/,等 LM 翻"
                )

            if session is not None and mod_added_count > 0:
                _safe_session_log(
                    session, f"  ✓ {modid}: +{mod_added_count} 個 key 進 assets/"
                )

            progress = idx / total_modids
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
            _safe_session_log(
                session, f"[MergeExt→Assets] 完成: {total_added} 個 key 已並入 assets/"
            )
        normalized = normalize_pending_extracted_wrappers(
            lang_output_dir,
            folder_names=pending_folder_names or ("待翻譯", "待翻譯整理需翻譯"),
        )
        if normalized and session is not None:
            _safe_session_log(
                session, f"[MergeExt→Assets] 已整理 {normalized} 個 pending 語言檔"
            )
        # 2026-08-04: per-mod cleanup 已在 loop 內處理,不再需要 batch cleanup
        yield {
            "progress": 1.0,
            "log": None,
            "error": had_errors,
            "message": "; ".join(error_details) if error_details else None,
        }

    except Exception as exc:  # noqa: BLE001 - 失敗已記錄，不中斷批次流程
        tb = traceback.format_exc()
        log_warning(f"[MergeExt→Assets] 錯誤: {exc!r}\n{tb}")
        if session is not None:
            _safe_session_log(session, f"[MergeExt→Assets] 錯誤: {exc!r}")
        yield {"progress": 1.0, "log": None, "error": True}
