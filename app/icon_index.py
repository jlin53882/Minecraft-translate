"""app/icon_index.py

用途：預建立 icon 索引（PR60 Phase 2）。
一次性建立 modid:key → (jar_path, png_path) 的 JSON 索引，
爾後每次開啟直接讀取 JSON，完全不做 JAR I/O，瞬間完成。
"""

from __future__ import annotations

import json
import hashlib
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterator

from translation_tool.utils.log_unit import log_info, log_warning


# ==================================================
# 核心資料結構
# ==================================================

# JAR 檔名 modid 截取 regex（module-level 常數，避免每次呼叫重建）
# 抓「第一段不以數字結尾」當 modid（如 cofh-core-1.21.jar → cofh-core）
# 不使用 bare \d，避免 appliedenergistics2-12.9.7 → appliedenergistics 的問題
_JAR_MODID_RE = re.compile(r'^([a-zA-Z0-9_][a-zA-Z0-9_\-]*?)(?:-\d|$)')

def _compute_modpack_hash(mods_dir: Path) -> str:
    """計算 modpack 的 stable hash（只用 JAR 檔名，忽略內容）。"""
    jar_files = sorted(j.name for j in mods_dir.glob("*.jar"))
    key_str = str(mods_dir.resolve()) + ":" + ",".join(jar_files)
    return hashlib.sha256(key_str.encode()).hexdigest()[:16]


def get_index_path(mods_dir: Path) -> Path:
    """取得該 modpack 的索引檔路徑。"""
    cache_dir = Path(__file__).parent.parent / ".icon_cache" / "icon_index"
    cache_dir.mkdir(parents=True, exist_ok=True)
    h = _compute_modpack_hash(mods_dir)
    return cache_dir / f"{h}.json"


# ==================================================
# JAR → icon 解析（給 worker thread 用）
# ==================================================

def _iter_entries_from_lang_files(zf: zipfile.ZipFile) -> Iterator[tuple[str, str]]:
    """從 JAR 的 lang 檔案列舉所有有效的 key。

    Yields: (key, value) — 只 yield 有意義的 key（不含 .* wildcard）
    """
    _MAX_LANG_SIZE = 10 * 1024 * 1024  # C-8 修復：lang 檔 10MB 上限
    for name in zf.namelist():
        if not (name.endswith(".lang") or "/lang/" in name or name.startswith("lang/")):
            continue
        # C-8 修復：讀取前檢查 file_size，防止 ZIP bomb
        try:
            info = zf.getinfo(name)
        except KeyError:
            continue
        if info.file_size > _MAX_LANG_SIZE:
            log_warning(f"[icon_index] ⚠️ 跳過過大 lang 檔：{name}（{info.file_size / 1024 / 1024:.1f}MB > 10MB）")
            continue
        try:
            content = zf.read(name).decode("utf-8", errors="ignore")
        except Exception:
            continue
        for line in content.splitlines():
            line = line.strip()
            if not line or "=" not in line:
                continue
            idx = line.index("=")
            key = line[:idx].strip()
            value = line[idx+1:].strip()
            if key and "." in key and value:
                yield key, value
        # 只讀第一個 lang 檔案（多數 JAR 只有一個）
        break


def _resolve_key_to_icon(jar_path: Path, modid: str, key: str) -> str | None:
    """對單一 key 進行完整的 icon 解析（ZIP讀取 + model lookup）。

    使用現有的 _try_extract_mod_icon_from_model，傳入 ZIP handle。
    回傳：IconRef URI 或 None
    """
    from app.views.icon_preview_view import (
        _try_extract_mod_icon_from_model,
    )
    from app.icon_reader import IconRef

    try:
        with zipfile.ZipFile(jar_path, "r") as zf:
            names = set(zf.namelist())
            result = _try_extract_mod_icon_from_model(
                jar_path, modid, zf, names, key=key
            )
            if result:
                tex_val, png_path = result
                return IconRef(jar_path, png_path).to_uri()
    except Exception:
        pass
    return None


def _process_single_jar(args: tuple[Path, str]) -> dict[str, str]:
    """Worker: 處理單一 JAR，建立該 JAR 所有 entry 的 icon 索引。

    回傳：{key: icon_uri} — 該 JAR 內所有有 icon 的 key mapping
    """
    jar_path, modid = args
    results: dict[str, str] = {}
    try:
        with zipfile.ZipFile(jar_path, "r") as zf:
            names = set(zf.namelist())
            from app.views.icon_preview_view import (
                _try_extract_mod_icon_from_model,
            )
            from app.icon_reader import IconRef

            _MAX_LANG_SIZE = 10 * 1024 * 1024  # C-8 修復：lang 檔 10MB 上限
            for name in zf.namelist():
                # 只讀 lang 檔案
                if not (name.endswith(".lang") or "/lang/" in name or name.startswith("lang/")):
                    continue
                # C-8 修復：讀取前檢查 file_size，防止 ZIP bomb
                try:
                    info = zf.getinfo(name)
                except KeyError:
                    continue
                if info.file_size > _MAX_LANG_SIZE:
                    log_warning(f"[icon_index] ⚠️ 跳過過大 lang 檔：{name}（{info.file_size / 1024 / 1024:.1f}MB > 10MB）")
                    continue
                try:
                    content = zf.read(name).decode("utf-8", errors="ignore")
                except Exception:
                    continue
                for line in content.splitlines():
                    line = line.strip()
                    if not line or "=" not in line:
                        continue
                    idx = line.index("=")
                    key = line[:idx].strip()
                    value = line[idx+1:].strip()
                    if not key or "." not in key:
                        continue

                    # 只處理有意义的 content key
                    parts = key.split(".")
                    if len(parts) < 2:
                        continue
                    prefix = parts[0]
                    if prefix not in ("item", "block", "entity", "enchantment", "effect",
                                      "potion", "biome", "attribute", "tile", "-effect"):
                        continue

                    result = _try_extract_mod_icon_from_model(
                        jar_path, modid, zf, names, key=key
                    )
                    if result:
                        tex_val, png_path = result
                        results[key] = IconRef(jar_path, png_path).to_uri()
                break  # 只讀第一個 lang 檔
    except Exception:
        pass
    return results


# ==================================================
# 公開 API
# ==================================================

def build_icon_index(mods_dir: Path, progress_cb=None) -> dict[str, str]:
    """使用 ThreadPoolExecutor 建立 icon 索引（Phase 2 主體）。

    流程：
        1. 列舉所有 JAR 及每個 JAR 的 modid
        2. 8 threads 平行處理每個 JAR
        3. 合併所有結果為單一 JSON 索引
        4. 寫入磁碟

    回傳：{key: icon_uri} 完整索引
    """
    import os

    # 找出所有 JAR 及其 modid（使用 module-level _JAR_MODID_RE）
    jars = sorted(mods_dir.glob("*.jar"))
    jar_modid_pairs: list[tuple[Path, str]] = []
    for jar in jars:
        m = _JAR_MODID_RE.match(jar.stem)
        modid = m.group(1) if m else jar.stem
        jar_modid_pairs.append((jar, modid))

    total = len(jar_modid_pairs)
    log_info(f"[IconIndex] 開始建立索引：{total} 個 JAR，使用 8 threads")

    index: dict[str, str] = {}
    done = 0

    # 8 threads，平行處理每個 JAR
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(_process_single_jar, (jar, modid)): (jar, modid)
            for jar, modid in jar_modid_pairs
        }
        for future in as_completed(futures):
            jar, modid = futures[future]
            done += 1
            try:
                jar_results = future.result()
                for key, uri in jar_results.items():
                    index[key] = uri
                if progress_cb:
                    progress_cb(done, total)
            except Exception as ex:
                log_warning(f"[IconIndex] JAR 處理失敗 {jar.name}: {ex}")
            if done % 50 == 0 or done == total:
                log_info(f"[IconIndex] 進度：{done}/{total} JARs，已建立 {len(index)} 個 icon 索引")

    log_info(f"[IconIndex] 索引建立完成：{len(index)} 個 icon 進入索引")
    return index


def save_icon_index(mods_dir: Path, index: dict[str, str]) -> Path:
    """將 icon 索引寫入磁碟（JSON 格式）。"""
    idx_path = get_index_path(mods_dir)
    data = {
        "version": 1,
        "modpack": str(mods_dir.resolve()),
        "count": len(index),
        "index": index,
    }
    idx_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    log_info(f"[IconIndex] 索引已儲存：{idx_path}")
    return idx_path


def load_icon_index(mods_dir: Path) -> dict[str, str] | None:
    """快速載入已建立的 icon 索引。找不到或格式不符回 None。"""
    idx_path = get_index_path(mods_dir)
    if not idx_path.exists():
        return None
    try:
        data = json.loads(idx_path.read_text(encoding="utf-8"))
        if data.get("version") != 1:
            return None
        if data.get("modpack") != str(mods_dir.resolve()):
            # modpack 路徑改了
            return None
        log_info(f"[IconIndex] 已載入索引：{data.get('count')} 個 icon（來源：{idx_path.name}）")
        return data["index"]
    except Exception as ex:
        log_warning(f"[IconIndex] 索引載入失敗：{ex}")
        return None
