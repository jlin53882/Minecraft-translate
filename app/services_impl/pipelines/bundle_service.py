"""Bundle pipeline service wrappers.

PR18：將 bundle 類 service 從 app.services.py 抽離到 pipelines 子模組，
由 app.services 持續做 façade / re-export，維持 UI import 相容。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import traceback

from app.services_impl.logging_service import GLOBAL_LOG_LIMITER
from translation_tool.core.output_bundler import bundle_outputs_generator

logger = logging.getLogger(__name__)

# 打包暫存區只收資源包內容；translation_map.json 等報表檔不應進入資源包
_STAGING_CONTENT_ROOT = "assets"
_STAGING_SKIP_DIRS = {"待翻譯", "待翻譯整理需翻譯"}


def _read_json_dict(path: str) -> dict | None:
    """讀取 JSON 物件；非 dict 或解析失敗時回傳 None。"""
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


_COMPARE_CHUNK = 1024 * 1024


def _same_file_content(src: str, dst: str) -> bool:
    """判斷 dst 是否與 src 位元組完全相同（大小不同直接判定不同，其餘逐塊比對）。

    **不能用 mtime 當相同的依據**：同步工具、備份還原或時間戳精度可能讓內容不同的兩個檔案
    大小與 mtime 都一樣，沿用舊檔會讓 ZIP 含過期資料，違反「增量結果 == 完整重建」。
    不使用 ``filecmp.cmp``：它以 (大小, mtime) 快取比對結果，同樣會被這種情況騙過。
    """
    try:
        if os.path.getsize(src) != os.path.getsize(dst):
            return False
        with open(src, "rb") as f1, open(dst, "rb") as f2:
            while True:
                chunk = f1.read(_COMPARE_CHUNK)
                if chunk != f2.read(len(chunk)):
                    return False
                if not chunk:
                    return True
    except OSError:
        return False


def _prepare_destination(dst: str) -> None:
    """建立 dst 的上層資料夾；若舊 staging 的檔案與資料夾型態互相衝突就先清掉。"""
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    parent = os.path.dirname(dst)
    probe = parent
    while probe and not os.path.exists(probe):
        probe = os.path.dirname(probe)
    # 往上找到第一個存在的路徑；若它是檔案（舊版同名檔案），要移除才能建立資料夾
    while probe and os.path.isfile(probe):
        os.remove(probe)
        probe = os.path.dirname(probe)
        while probe and not os.path.exists(probe):
            probe = os.path.dirname(probe)
    os.makedirs(parent, exist_ok=True)


def _copy_file(src: str, dst: str) -> None:
    _prepare_destination(dst)
    shutil.copy2(src, dst)


def _write_bytes(dst: str, data: bytes) -> None:
    _prepare_destination(dst)
    with open(dst, "wb") as f:
        f.write(data)


def _canonical_path(rel_path: str, canon: dict[str, str]) -> str:
    """逐層取得「第一次出現的拼法」（大小寫不敏感的檔案系統上，同一路徑只有一種拼法）。

    與舊版完整重建一致：先寫入的來源決定資料夾與檔名的大小寫，後面的來源寫進同一個位置。
    ``canon`` 以 ``os.path.normcase`` 的結果為鍵，記錄每一層路徑的標準拼法。
    """
    current = ""
    for part in rel_path.split(os.sep):
        candidate = os.path.join(current, part) if current else part
        current = canon.setdefault(os.path.normcase(candidate), candidate)
    return current


def _collect_bundle_sources(
    sources: list[str],
) -> tuple[dict[str, tuple[str, list[str]]], dict[str, str]]:
    """依來源優先序收集各輸出路徑對應的來源檔案。

    Returns:
        (plan, canon)。plan：{路徑比對鍵: (staging 內的標準相對路徑, [來源檔案…（優先序由低到高）])}；
        canon：每一層路徑（資料夾與檔案）的標準拼法。
        比對鍵用 ``os.path.normcase``，使 Windows 上僅大小寫不同的路徑視為同一個檔案。
    """
    plan: dict[str, tuple[str, list[str]]] = {}
    canon: dict[str, str] = {}
    for source in sources:
        content_root = os.path.join(source, _STAGING_CONTENT_ROOT)
        if not os.path.isdir(content_root):
            continue
        for root, dirs, files in os.walk(content_root):
            dirs[:] = [d for d in dirs if d not in _STAGING_SKIP_DIRS]
            rel_root = os.path.relpath(root, source)
            for name in files:
                rel_path = _canonical_path(os.path.join(rel_root, name), canon)
                key = os.path.normcase(rel_path)
                if key not in plan:
                    plan[key] = (rel_path, [])
                plan[key][1].append(os.path.join(root, name))
    return plan, canon


def _normalize_staging_case(staging_dir: str, canon: dict[str, str]) -> None:
    """把 staging 內「只有大小寫不同」的既有資料夾與檔案改成標準拼法。

    大小寫不敏感的檔案系統（Windows）上，來源改名大小寫（``modid`` → ``ModID``）時，
    寫入會落在既有的實體項目上、拼法維持舊的；ZIP 的項目名稱來自 staging 的實體路徑，
    所以必須先改名，完整重建與增量結果才會一致。大小寫敏感的系統上，兩種拼法本來就是
    不同的路徑，不會進到這裡（``normcase`` 不改變大小寫）。
    """
    for root, dirs, files in os.walk(staging_dir):
        rel_root = os.path.relpath(root, staging_dir)
        for names in (dirs, files):
            for index, name in enumerate(names):
                rel = name if rel_root == "." else os.path.join(rel_root, name)
                wanted = canon.get(os.path.normcase(rel))
                if wanted is None or wanted == rel:
                    continue
                new_name = os.path.basename(wanted)
                os.rename(os.path.join(root, name), os.path.join(root, new_name))
                names[index] = new_name  # os.walk 之後會以新名稱往下走


def _prune_staging(staging_dir: str, expected: set[str]) -> int:
    """刪除 staging 內不在預期集合的檔案與空資料夾，回傳刪除的檔案數。"""
    removed = 0
    for root, dirs, files in os.walk(staging_dir, topdown=False):
        for name in files:
            path = os.path.join(root, name)
            if os.path.normcase(os.path.relpath(path, staging_dir)) not in expected:
                os.remove(path)
                removed += 1
        if root != staging_dir:
            try:
                os.rmdir(root)  # 只會刪掉空資料夾
            except OSError:
                pass
    return removed


def build_bundle_staging(sources: list[str], staging_dir: str) -> dict:
    """把多個輸出根目錄的 assets/ 依序疊加到 staging_dir，供打包使用。

    後面的來源優先：同路徑的 JSON 物件逐 key 合併（後者覆蓋前者），
    其他檔案直接覆蓋。只處理各來源底下的 assets/，並略過待翻譯資料夾。

    staging 是增量更新的：不再整個刪除重建，而是先算出每個輸出檔「應有的內容」，
    與現有 staging 內容相同就不寫入，最後刪掉不在預期集合內的殘留檔案。結果與完整
    重建一致；沒有 manifest 或版本標記，每次都由來源重新推導，因此不會有過期狀態，
    中斷後下一次執行也會收斂到同一結果。內容是否相同只看位元組（不依 mtime）；大小寫不敏感的
    檔案系統上，僅大小寫不同的既有資料夾與檔案會先改名成來源的拼法。

    Args:
        sources: 依優先序由低到高排列的來源根目錄（不存在者略過）。
        staging_dir: 暫存目錄；不存在會建立，內容會被更新成與來源一致。

    Returns:
        {"copied": int, "merged": int, "written": int, "unchanged": int, "removed": int}
        ``copied`` / ``merged`` 是處理的來源檔案數（與是否需要寫入無關，呼叫端用來判斷
        是否有可打包內容）；``written`` / ``unchanged`` 是實際寫入與沿用的輸出檔數；
        ``removed`` 是刪除的殘留檔案數。
    """
    os.makedirs(staging_dir, exist_ok=True)

    plan, canon = _collect_bundle_sources(sources)
    _normalize_staging_case(staging_dir, canon)

    copied = merged = written = unchanged = 0
    expected: set[str] = set()
    for key, (rel_path, srcs) in plan.items():
        dst = os.path.join(staging_dir, rel_path)
        expected.add(key)

        # 逐一疊加來源，語意與舊版「依序寫入 staging」相同：JSON 物件與前一份合併，
        # 否則整個覆蓋。state 要嘛是來源檔路徑（原樣複製），要嘛是合併後的 dict。
        state: str | dict = srcs[0]
        copied += 1
        for src in srcs[1:]:
            if src.lower().endswith(".json"):
                base = state if isinstance(state, dict) else _read_json_dict(state)
                extra = _read_json_dict(src)
                if base is not None and extra is not None:
                    base.update(extra)
                    state = base
                    merged += 1
                    continue
            state = src
            copied += 1

        if isinstance(state, dict):
            data = json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8")
            try:
                with open(dst, "rb") as f:
                    same = f.read() == data
            except OSError:
                same = False
            if not same:
                _write_bytes(dst, data)
        else:
            same = _same_file_content(state, dst)
            if not same:
                _copy_file(state, dst)
        if same:
            unchanged += 1
        else:
            written += 1

    removed = _prune_staging(staging_dir, expected)
    return {
        "copied": copied,
        "merged": merged,
        "written": written,
        "unchanged": unchanged,
        "removed": removed,
    }


def run_bundling_service(
    input_root_dir: str,
    output_zip_path: str,
    description: str = "",
    min_format: int = 0,
    max_format: int = 0,
    pack_image_path: str | None = None,
    extra_folders: list[str] | None = None,
    force_rebuild: bool = False,
):
    """執行此 generator 並逐步回報進度（yield update dict）。"""
    try:
        for update_dict in bundle_outputs_generator(
            input_root_dir,
            output_zip_path,
            description=description,
            min_format=min_format,
            max_format=max_format,
            pack_image_path=pack_image_path,
            extra_folders=extra_folders,
            force_rebuild=force_rebuild,
        ):
            filtered = GLOBAL_LOG_LIMITER.filter(update_dict)
            if filtered is not None:
                yield filtered
    except Exception as e:  # noqa: BLE001
        full_traceback = traceback.format_exc()
        logger.error(f"[致命錯誤] 打包服務失敗：{e}\n{full_traceback}")
        yield {
            "log": f"[致命錯誤] 打包服務失敗：{e}\n{full_traceback}",
            "error": True,
            "progress": 0,
        }
