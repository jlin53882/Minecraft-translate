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


def build_bundle_staging(sources: list[str], staging_dir: str) -> dict:
    """把多個輸出根目錄的 assets/ 依序疊加到 staging_dir，供打包使用。

    後面的來源優先：同路徑的 JSON 物件逐 key 合併（後者覆蓋前者），
    其他檔案直接覆蓋。只處理各來源底下的 assets/，並略過待翻譯資料夾。

    Args:
        sources: 依優先序由低到高排列的來源根目錄（不存在者略過）。
        staging_dir: 暫存目錄；呼叫前會先清空。

    Returns:
        {"copied": int, "merged": int}
    """
    if os.path.isdir(staging_dir):
        shutil.rmtree(staging_dir)
    os.makedirs(staging_dir, exist_ok=True)

    copied = merged = 0
    for source in sources:
        content_root = os.path.join(source, _STAGING_CONTENT_ROOT)
        if not os.path.isdir(content_root):
            continue
        for root, dirs, files in os.walk(content_root):
            dirs[:] = [d for d in dirs if d not in _STAGING_SKIP_DIRS]
            rel_root = os.path.relpath(root, source)
            for name in files:
                src = os.path.join(root, name)
                dst = os.path.join(staging_dir, rel_root, name)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if name.lower().endswith(".json") and os.path.exists(dst):
                    base, extra = _read_json_dict(dst), _read_json_dict(src)
                    if base is not None and extra is not None:
                        base.update(extra)
                        with open(dst, "w", encoding="utf-8") as f:
                            json.dump(base, f, ensure_ascii=False, indent=2)
                        merged += 1
                        continue
                shutil.copy2(src, dst)
                copied += 1
    return {"copied": copied, "merged": merged}


def run_bundling_service(
    input_root_dir: str,
    output_zip_path: str,
    description: str = "",
    min_format: int = 0,
    max_format: int = 0,
    pack_image_path: str | None = None,
    extra_folders: list[str] | None = None,
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
        ):
            filtered = GLOBAL_LOG_LIMITER.filter(update_dict)
            if filtered is not None:
                yield filtered
    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"[致命錯誤] 打包服務失敗：{e}\n{full_traceback}")
        yield {
            "log": f"[致命錯誤] 打包服務失敗：{e}\n{full_traceback}",
            "error": True,
            "progress": 0,
        }
