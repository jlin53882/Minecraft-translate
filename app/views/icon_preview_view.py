"""app/views/icon_preview_view.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import flet as ft
import json
import os
import hashlib
import platform
import re
import zipfile
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone
from app.ui import theme
from translation_tool.utils.log_unit import log_info, log_warning, log_error
from types import SimpleNamespace

from translation_tool.utils.safe_json_loader import load_json_auto_encoding
from translation_tool.core.lang_item_row import LangItemRow

import unicodedata
import threading

# ==================================================
# 實驗性功能開關
# ==================================================
_ENABLE_JAR_ICON = True  # 已啟用（Model JSON 解析 + 批次 ZIP icon 提取）

# 真正需要遊戲圖示的 key 前綴（只有這些才 fallback 到 logo.png）
# 不在清單裡的 key（如 _comment、advancements.*、recipe_type、jei.* 等）不該有 icon
_CONTENT_ICON_PREFIXES = frozenset([
    "item", "block", "entity", "enchantment", "effect", "potion", "biome",
    "attribute", "tile", "-effect",
])

def _key_needs_icon(key: str) -> bool:
    """判斷 key 是否為需要 icon 的遊戲內容。

    只有 item/block/entity 等前綴才需要 icon。
    metadata key（如 _comment、advancements.*、jei.*）完全不該有 icon。
    """
    if "." not in key:
        return False  # 完全沒有 namespace，不可能是遊戲內容
    prefix = key.split(".")[0]
    return prefix in _CONTENT_ICON_PREFIXES

# ==================================================
# JAR Icon 提取輔助函式（Phase 1: Model JSON 解析）
# ==================================================

def _get_icon_cache_dir() -> Path:
    """取得 icon 快取根目錄（統一至 .icon_cache/jar_icons/）。"""
    return Path(__file__).parent.parent.parent / ".icon_cache" / "jar_icons"


def _get_model_index_cache_dir() -> Path:
    """取得 model index 快取目錄（.icon_cache/model_index/）。"""
    return Path(__file__).parent.parent.parent / ".icon_cache" / "model_index"


def _get_jar_hash(jar_path: Path) -> str:
    """計算 JAR 的 hash（mtime + size），用於 cache 失效判斷。"""
    stat = jar_path.stat()
    return f"mtime:{stat.st_mtime:.0f}_size:{stat.st_size}"


def _migrate_old_icon_cache(source_root: Path) -> bool:
    """向後相容：將舊路徑的 icon cache 搬移至新路徑。

    舊路徑：source_root/_icon_preview/jar_icons/
    新路徑：.icon_cache/jar_icons/

    搬移條件：
        - 舊路徑存在
        - 新路徑尚不存在，或新路徑為空目錄

    回傳：
        True 表示有搬移，False 表示無需搬移
    """
    import shutil

    old_path = source_root / "_icon_preview" / "jar_icons"
    new_path = _get_icon_cache_dir()

    if not old_path.exists():
        # 舊路徑不存在，無需搬移
        return False

    # 新路徑已存在且有內容，不覆蓋
    if new_path.exists() and any(new_path.iterdir()):
        log_info(f"[IconPreview] 新 icon cache 已存在，放棄搬移舊路徑: {old_path}")
        return False

    # 確保新路徑的父目錄存在
    new_path.parent.mkdir(parents=True, exist_ok=True)

    # 搬移所有檔案
    files_moved = 0
    for old_file in old_path.glob("*.png"):
        new_file = new_path / old_file.name
        if not new_file.exists():
            shutil.move(str(old_file), str(new_file))
            files_moved += 1

    log_info(f"[IconPreview] 已將 {files_moved} 個 icon 檔案從舊路徑搬移至新路徑: {old_path} → {new_path}")
    return True


_INVALID_FN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_filename_key(key: str) -> str:
    """將 lang key 轉為可用於檔名的字串。

    處理的問題：
    - key 的最後一段可能含 Windows 不允許的字元（如 \\）
    - key 可能含空白或 unicode 符號

    處理方式：
    - 移除 Windows 檔名禁用字元（\\ / : * ? " < > |）
    - 將空白替換為底線
    - 限制長度（最多 64 字）避免路徑過長
    """
    suffix = key.split(".")[-1]
    safe = _INVALID_FN_CHARS.sub("_", suffix)
    safe = safe.strip().replace(" ", "_")
    # 避免路徑過長（Windows MAX_PATH 260）
    return safe[:64] if len(safe) > 64 else safe


def _load_model_index_from_cache(jar_path: Path, modid: str) -> dict | None:
    """嘗試從磁碟讀取 model index cache。

    失效條件：JAR 的 mtime/size 改變，或 cache 檔不存在/格式無效。

    回傳：
        model_index dict（name → [路徑列表]），或 None（cache miss）
    """
    cache_dir = _get_model_index_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe_jar_name = jar_path.stem  # stem 已剝除副檔名
    cache_file = cache_dir / f"{safe_jar_name}.json"

    if not cache_file.exists():
        return None

    try:
        with open(cache_file, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return None

    # 檢查 jar_hash 是否匹配
    current_hash = _get_jar_hash(jar_path)
    if data.get("jar_hash") != current_hash:
        return None

    if data.get("modid") != modid:
        return None

    return data.get("index")


def _save_model_index_to_cache(jar_path: Path, modid: str, model_index: dict):
    """將 model index 寫入磁碟 cache（atomic write）。"""
    cache_dir = _get_model_index_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe_jar_name = jar_path.stem  # stem 已剝除副檔名
    cache_file = cache_dir / f"{safe_jar_name}.json"

    data = {
        "jar_name": jar_path.name,
        "jar_hash": _get_jar_hash(jar_path),
        "modid": modid,
        "index": model_index,
    }

    tmp = cache_dir / f"{cache_file.stem}.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(cache_file)


def _build_model_index(names: list[str], modid: str) -> dict[str, list[str]]:
    """動態掃描所有 .json model 檔，建立 name → [路徑列表] index。

    name = 相對路徑（保留子目錄），例如 block/restonia_crystal_block。
    同一個 name 可能來自不同子目錄（block/ vs item/），全部保留。
    """
    index: dict[str, list[str]] = {}
    prefix = f"assets/{modid}/models/"

    for n in names:
        if not (n.startswith(prefix) and n.endswith(".json")):
            continue
        rel = n[len(prefix):]
        name = rel.replace(".json", "")
        # 保留子目錄前綴（例如 block/restonia_crystal_block）
        # 這樣 _try_extract_mod_icon_from_model 可以用完整路徑做精準 lookup
        if name not in index:
            index[name] = []
        index[name].append(n)

    return index


def _get_texture_value(model_data: dict) -> str | None:
    """從 model JSON 的 textures 欄位取值（無白名單優先順序）。

    邏輯：
        - 只有 1 個 key → 直接取那個值
        - 超過 1 個 key → 依序取：layer0 → front → particle → 任意第一個
    """
    textures = model_data.get("textures", {})
    if not textures:
        return None

    if len(textures) == 1:
        return list(textures.values())[0]

    for key in ["layer0", "front", "particle"]:
        if key in textures:
            return textures[key]

    return list(textures.values())[0]


def _follow_parent_chain(
    model_path: str,
    names: set[str],
    modid: str,
    zf: zipfile.ZipFile,
    visited: set[str] | None = None,
) -> str | None:
    """沿 parent chain 遞迴向上找，直到找到有 textures 的 model。

    遇到 minecraft: 開頭的 parent 直接跳過（不處理 Minecraft 內建資源）。
    """
    if visited is None:
        visited = set()

    if model_path in visited or model_path not in names:
        return None
    visited.add(model_path)

    # C-9 修復：讀取前檢查 file_size，防止過大 JSON model 檔
    try:
        info = zf.getinfo(model_path)
    except KeyError:
        return None
    _MAX_MODEL_SIZE = 10 * 1024 * 1024  # 10MB
    if info.file_size > _MAX_MODEL_SIZE:
        return None

    try:
        raw = zf.read(model_path).decode("utf-8", errors="replace")
        data = json.loads(raw)
    except Exception:
        return None

    tex_val = _get_texture_value(data)
    if tex_val:
        return tex_val

    parent = data.get("parent")
    if not parent:
        return None

    if ":" in parent:
        ns, path = parent.split(":", 1)
        if ns == modid:
            parent_path = f"assets/{ns}/models/{path}.json"
        elif ns == "minecraft":
            return None  # 跳過 Minecraft 內建資源
        else:
            return None
    else:
        base = str(Path(model_path).parent).replace("\\", "/")
        parent_path = f"{base}/{parent}.json"

    return _follow_parent_chain(parent_path, names, modid, zf, visited)


def _texture_to_png_path(tex_val: str) -> str | None:
    """將 texture value（namespace:path）轉換為 JAR 內的 PNG 路徑。

    格式：namespace:path → assets/namespace/textures/path.png
    """
    if not tex_val or ":" not in tex_val:
        return None
    ns, path = tex_val.split(":", 1)
    return f"assets/{ns}/textures/{path}.png"


def _try_extract_mod_icon_from_model(
    jar_path: Path,
    modid: str,
    zf: zipfile.ZipFile,
    names: set[str],
    key: str | None = None,
) -> tuple[str, str] | None:
    """嘗試從 model JSON 解析 mod icon。

    流程：
        1. 建立/讀取 model index（使用 cache）
        2. 如果有 key，先用 key 查 item 自己的 model texture（精準匹配）
        3. 移除 logo/icon.png fallback（錯誤的 icon 比沒有更糟）
           （但只有當 key 的 namespace 與 modid 一致時才套用 fallback）
        4. 使用 texture fallback 策略取值

    回傳：
        (texture_value, png_path) 或 None
    """
    model_index = _load_model_index_from_cache(jar_path, modid)
    if model_index is None:
        model_index = _build_model_index(list(names), modid)
        _save_model_index_to_cache(jar_path, modid, model_index)

    # ===== 優先：嘗試用 key 查 item 自己的 model texture =====
    if key:
        # 將 key 轉為 model name
        # 例如：block.actuallyadditions.restonia_crystal_block → block/restonia_crystal_block
        #       item.actuallyadditions.drill → item/drill
        # 原理：key = "<prefix>.<modid>.<name>"，去掉 modid 前綴就是 model name
        prefix = key.split(".")[0]  # "block" 或 "item" 等
        rest = key[len(prefix) + 1 + len(modid) + 1:]  # "restonia_crystal_block"
        model_name = f"{prefix}/{rest}"  # "block/restonia_crystal_block"

        if model_name in model_index:
            for model_path in model_index[model_name]:
                tex_val = _follow_parent_chain(model_path, names, modid, zf)
                if not tex_val:
                    continue
                png_path = _texture_to_png_path(tex_val)
                if png_path and png_path in names:
                    return tex_val, png_path

    # ===== Fallback：找 icon/logo/item_icon/block_icon 模型 =====
    # 限制：只有當 key 的 namespace 與 modid 一致時才套用 fallback
    # 原因：icon/logo 模型是該 mod 的專屬資源（如 assets/actuallyadditions/models/icon.json）
    # 不該用在 minecraft 命名空間的 key（如 block.minecraft.banner.actuallyadditions.*）
    # key 格式：<prefix>.<namespace>.<name> 或 <prefix>.<name>（vanilla 無 namespace portion）
    # namespace portion = key.split(".")[1]
    # 只有當有 namespace portion（key 有 >= 3 parts）且 namespace != modid 時才阻止
    if key:
        key_parts = key.split(".")
        if len(key_parts) >= 3:
            key_ns = key_parts[1]  # namespace portion
            if key_ns != modid:
                return None  # namespace 不一致，直接回 None，不做任何 fallback

    # 當 model lookup 失敗時，不做任何 logo/icon.png fallback，直接回 None
    return None


def _extract_jar_icon(jar_path: Path, modid: str, icon_cache_root: Path, key: str) -> Path | None:
    """從 JAR 中提取 mod icon 並快取到磁碟（Phase 1: Model JSON 解析）。

    支援（按優先順序）：
        1. Model JSON 解析（layer0 > front > particle > 第一個）+ parent chain 遞迴
        2. assets/<modid>/icon.png（Fabric 標準）
        3. assets/<modid>/textures/logo.png（通用 mod logo）
        4. NeoForge: neoforge.mods.toml → logoFile

    參數：
        jar_path: JAR 檔案路徑
        modid: mod ID
        icon_cache_root: icon 快取根目錄（.icon_cache/jar_icons/）
        key: lang key（用於產生 unique icon 檔名）

    回傳：
        提取後的圖示路徑，或 None（找不到或提取失敗）
    """
    try:
        with zipfile.ZipFile(jar_path, "r") as zf:
            names = set(zf.namelist())

            # C-9 修復：所有 ZIP 讀取前檢查 file_size，防止 ZIP bomb
            _MAX_ICON_SIZE = 512 * 1024  # 512KB icon 圖片上限
            _MAX_TOML_SIZE = 10 * 1024 * 1024  # 10MB toml 上限

            def _check_size(zf_obj, name, max_sz):
                """讀取前檢查 ZIP 成員大小，超限拋例外。"""
                try:
                    info = zf_obj.getinfo(name)
                except KeyError:
                    raise RuntimeError(f"ZIP 成員 {name} 不存在")
                if info.file_size > max_sz:
                    raise RuntimeError(f"ZIP 成員 {name} 大小（{info.file_size/1024/1024:.1f}MB）超過上限（{max_sz/1024/1024:.1f}MB）")

            # ===== Phase 1: Model JSON 解析（最高優先）=====
            # P1 修復：若 PNG 大小超過限制，不拋錯而是用 continue 讓 Phase 2/3 接手
            # 否則 Phase 1 的過大 PNG 會導致整個 extraction abort
            try:
                result = _try_extract_mod_icon_from_model(jar_path, modid, zf, names, key=key)
                if result:
                    tex_val, png_path = result
                    _check_size(zf, png_path, _MAX_ICON_SIZE)
                    icon_data = zf.read(png_path)
                    icon_cache_root.mkdir(parents=True, exist_ok=True)
                    out_path = icon_cache_root / f"{modid}_{jar_path.stem}_{_safe_filename_key(key)}.png"
                    out_path.write_bytes(icon_data)
                    log_info(f"[IconPreview] Model JSON icon: {modid} → {png_path} (tex={tex_val})")
                    return out_path
            except RuntimeError as ex:
                log_info(f"[IconPreview] Phase 1 失敗（{ex}），繼續 Phase 2...")

            # ===== Fallback: assets/<modid>/icon.png（Fabric 標準）=====
            fabric_icon = f"assets/{modid}/icon.png"
            if fabric_icon in names:
                _check_size(zf, fabric_icon, _MAX_ICON_SIZE)
                icon_data = zf.read(fabric_icon)
                icon_cache_root.mkdir(parents=True, exist_ok=True)
                out_path = icon_cache_root / f"{modid}_{jar_path.stem}_{_safe_filename_key(key)}.png"
                out_path.write_bytes(icon_data)
                log_info(f"[IconPreview] 提取 Fabric icon.png: {modid}")
                return out_path

            # ===== Fallback: assets/<modid>/textures/*.png（Fabric glob）=====
            import re
            textures_pattern = re.compile(r"^assets/" + re.escape(modid) + r"/textures/.+\.png$")
            texture_files = sorted(n for n in names if textures_pattern.match(n))
            if texture_files:
                _check_size(zf, texture_files[0], _MAX_ICON_SIZE)
                icon_data = zf.read(texture_files[0])
                icon_cache_root.mkdir(parents=True, exist_ok=True)
                out_path = icon_cache_root / f"{modid}_{jar_path.stem}_{_safe_filename_key(key)}.png"
                out_path.write_bytes(icon_data)
                log_info(f"[IconPreview] 提取 Fabric texture icon: {modid} → {texture_files[0]}")
                return out_path

            # ===== Fallback: assets/<modid>/textures/logo.png =====
            logo_texture = f"assets/{modid}/textures/logo.png"
            if logo_texture in names:
                _check_size(zf, logo_texture, _MAX_ICON_SIZE)
                icon_data = zf.read(logo_texture)
                icon_cache_root.mkdir(parents=True, exist_ok=True)
                out_path = icon_cache_root / f"{modid}_{jar_path.stem}_{_safe_filename_key(key)}.png"
                out_path.write_bytes(icon_data)
                log_info(f"[IconPreview] 提取 logo.png: {modid}")
                return out_path

            # ===== Fallback: NeoForge logoFile =====
            neoforge_toml = "META-INF/neoforge.mods.toml"
            if neoforge_toml in names:
                _check_size(zf, neoforge_toml, _MAX_TOML_SIZE)
                try:
                    toml_content = zf.read(neoforge_toml).decode("utf-8")
                except UnicodeDecodeError:
                    toml_content = None

                if toml_content:
                    logo_match = re.search(r'logoFile\s*=\s*"([^"]+\.png)"', toml_content)
                    if logo_match:
                        logo_path = logo_match.group(1)
                        if logo_path in names:
                            _check_size(zf, logo_path, _MAX_ICON_SIZE)
                            icon_data = zf.read(logo_path)
                            icon_cache_root.mkdir(parents=True, exist_ok=True)
                            out_path = icon_cache_root / f"{modid}_{jar_path.stem}_{_safe_filename_key(key)}.png"
                            out_path.write_bytes(icon_data)
                            log_info(f"[IconPreview] 提取 NeoForge logoFile: {modid} → {logo_path}")
                            return out_path

    except Exception as ex:
        log_warning(f"[IconPreview] 提取 JAR icon 失敗: {jar_path.name} / {modid} → {ex}")

    return None


# ==================================================
# 批次 Icon 提取（每個 JAR 只開一次 ZIP）
# ==================================================

def _batch_extract_jar_icons(jar_to_entries: dict[str, list], icon_cache_root: Path, source_root: Path, progress_cb=None) -> int:
    """批次處理多個 JAR 的 icon 提取（支援預建立索引 + ThreadPoolExecutor）。

    PR60 優化架構：
        1. 嘗試從預建立索引讀取（instant，零 JAR I/O）
        2. 若無索引：使用 ThreadPoolExecutor 8 threads 建立索引（1-2 分鐘）
        3. 若索引正在建立中（另一執行緒）：降級為 ThreadPoolExecutor 即時處理

    參數：
        jar_to_entries: {jar_name: [entries]}，同一個 JAR 的所有 entry
        icon_cache_root: （已廢棄，參數保留但不再使用）
        source_root: 資料根目錄（JAR 所在位置）
        progress_cb: 進度回呼（可選）

    回傳：
        處理的 JAR 數量
    """
    from app.icon_reader import IconRef
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # ===== Phase 0: Per-batch in-memory cache：同一 (modid, key) 在同一批次內不重複解析 =====
    _result_cache: dict[tuple[str, str], str | None] = {}

    # ===== Phase 1: 嘗試從預建立索引讀取（instant）=====
    icon_index = None
    try:
        from app import icon_index as idx_module
        icon_index = idx_module.load_icon_index(source_root)
    except Exception:
        pass

    if icon_index is not None:
        # 索引存在：直接用索引，完全不做 JAR I/O
        applied = 0
        for jar_name, entries in jar_to_entries.items():
            for e in entries:
                if not (hasattr(e, "modid") and hasattr(e, "key")):
                    continue
                key = e.key
                if key in icon_index:
                    e.icon_path = icon_index[key]
                    applied += 1
        if progress_cb:
            progress_cb(len(jar_to_entries), len(jar_to_entries))
        log_info(f"[IconPreview] 索引命中：{applied} 個 entry 直接套用 icon")
        return len(jar_to_entries)

    # ===== Phase 2: 無索引 → ThreadPoolExecutor 即時處理 =====
    log_info(f"[IconPreview] 無索引，啟動 ThreadPoolExecutor 處理 {len(jar_to_entries)} 個 JAR")

    def _process_jar(jar_name: str) -> dict[str, str | None]:
        """Worker：處理單一 JAR，回傳 {key: icon_uri or None}。"""
        jar_path = source_root / jar_name
        result_map: dict[str, str | None] = {}
        if not jar_path.exists():
            return result_map
        try:
            with zipfile.ZipFile(jar_path, "r") as zf:
                names = set(zf.namelist())
                for e in jar_to_entries.get(jar_name, []):
                    if not (hasattr(e, "modid") and hasattr(e, "key")):
                        continue
                    modid = e.modid
                    key = e.key
                    # 預先過濾：不需要 icon 的 key 直接跳過，不浪費 lookup 時間
                    if not _key_needs_icon(key):
                        continue
                    # Per-(modid, key) cache：同 (modid, key) 在同批次不重複解析
                    cache_key = (modid, key)
                    if cache_key in _result_cache:
                        result_map[key] = _result_cache[cache_key]
                        continue
                    res = _try_extract_mod_icon_from_model(jar_path, modid, zf, names, key=key)
                    if res:
                        tex_val, png_path = res
                        uri = IconRef(jar_path, png_path).to_uri()
                        result_map[key] = uri
                        _result_cache[cache_key] = uri
                    else:
                        _result_cache[cache_key] = None
                        result_map[key] = None
        except Exception:
            pass
        return result_map

    processed = 0
    total = len(jar_to_entries)
    # Flet 環境限制：只允許 2 個執行緒真正並發，設 4 workers 減少排程開銷
    # （不宜設太高，會加劇執行緒競爭）
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(_process_jar, jar_name): jar_name for jar_name in jar_to_entries}
        for future in as_completed(futures):
            jar_name = futures[future]
            try:
                entry_icon_paths = future.result()
                for e in jar_to_entries.get(jar_name, []):
                    if hasattr(e, "key") and e.key in entry_icon_paths:
                        uri = entry_icon_paths[e.key]
                        if uri:
                            e.icon_path = uri
            except Exception:
                pass
            processed += 1
            if progress_cb:
                progress_cb(processed, total)
                if processed % 50 == 0:
                    log_info(f"[IconPreview] 處理進度：{processed}/{total} JARs")

    log_info(f"[IconPreview] ThreadPoolExecutor 完成：{processed} 個 JAR")
    return processed


# ==================================================
# L2 磁碟快取工具函式
# ==================================================
def _get_cache_dir() -> Path:
    """取得 L2 快取目錄（專案根目錄）。"""
    return Path(__file__).parent.parent.parent / ".icon_cache"


def _compute_cache_key(source_root: Path) -> str:
    """計算快取 key：只看 JAR 檔案名稱，不算內容。

    注意：key 只包含 JAR 的檔名。這樣：
    - 新增/移除 JAR → key 改變 → 快取失效
    - JAR 內容變了但檔名不變 → 不會自動失效（已知限制）
    """
    jar_files = sorted([j.name for j in source_root.glob("*.jar")])
    key_str = str(source_root.resolve()) + ":" + ",".join(jar_files)
    return hashlib.sha256(key_str.encode()).hexdigest()[:16]


def _load_entries_cache_l2(source_root: Path) -> list | None:
    """讀取 L2 磁碟快取。回傳 None 表示快取失效。

    失效條件：
    - 快取檔案不存在
    - JSON 解析失敗
    - version 不為 1
    - source_root 不符
    """
    cache_dir = _get_cache_dir()
    cache_file = cache_dir / f"{_compute_cache_key(source_root)}.json"

    if not cache_file.exists():
        return None

    try:
        with open(cache_file, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return None  # 損壞的快取視為失效

    # 版本檢查
    if data.get("version") != 1:
        return None

    # 路徑檢查
    if data.get("source_root") != str(source_root):
        return None

    return data.get("entries", [])


def _save_entries_cache_l2(source_root: Path, entries: list):
    """寫入 L2 磁碟快取（atomic write）。"""
    import tempfile

    cache_dir = _get_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{_compute_cache_key(source_root)}.json"

    # Atomic write：用 tmp 檔再 rename
    tmp = cache_dir / f"{cache_file.stem}.tmp"
    # 將 entries 轉為可序列化格式
    serializable_entries = []
    for e in entries:
        if hasattr(e, "__dict__"):
            serializable_entries.append(e.__dict__)
        elif isinstance(e, dict):
            serializable_entries.append(e)
        else:
            serializable_entries.append({
                "modid": str(e.modid), "key": str(e.key), 
                "en": str(e.en), "zh_tw": str(e.zh_tw),
                "source_jar": getattr(e, "source_jar", ""),
                "icon_path": getattr(e, "icon_path", None),  # [FIX] 加入 icon_path
            })

    data = {
        "version": 1,
        "source_root": str(source_root),
        "entries": serializable_entries,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(cache_file)  # 跨平台 atomic replace（Python 3.3+，自動覆蓋目標檔案）


# ==================================================
# Phase 進度條輔助
# ==================================================
def _make_progress_callback(obj, phase: str, total: int):
    """建立 Phase 進度 callback。

    參數：
        obj: IconPreviewView 例項（需有 progress_bar, progress_text, update 方法）
        phase: Phase 顯示文字
        total: 總步數
    """
    def callback(processed: int, total: int):
        # 安全檢查：測試環境或 UI 未初始化時不拋例外
        if not hasattr(obj, 'progress_text') or not hasattr(obj, 'progress_bar'):
            return
        obj.progress_text.value = f"[{phase}] {processed} / {total}"
        obj.progress_bar.value = processed / total if total > 0 else 0
        obj.update()
    return callback


def _show_progress_phase(obj, phase: str, current: int, total: int):
    """更新 Phase 進度顯示（並墊底一次）。"""
    # 安全檢查：測試環境或 UI 未初始化時不拋例外
    if not hasattr(obj, 'progress_text') or not hasattr(obj, 'progress_bar'):
        return
    obj.progress_text.value = f"[{phase}] {current} / {total}"
    obj.progress_bar.value = current / total if total > 0 else 0
    obj.progress_bar.visible = True
    obj.update()


def to_halfwidth(text):
    """
    將字串正規化為半形（NFKC）
    - 只處理 str
    - 非 str 原樣返回（安全）
    """
    if not isinstance(text, str):
        return text
    return unicodedata.normalize("NFKC", text)

class IconPreviewView(ft.Column):
    """
    Icon / 翻譯校對 View（模組分層版）
    - 第一層：模組清單
    - 第二層：單一模組翻譯 + icon 校對
    """

    def __init__(self, page: ft.Page):
        """初始化 IconPreviewView。

        參數：
            page: Flet Page 物件
        """
        super().__init__(expand=True, spacing=8)
        self.page = page

        # =========================
        # 使用者選擇的資料夾
        # =========================
        self.source_root: Path | None = None  # 原文（en_us + textures）
        self.review_root: Path | None = None  # 校對（zh_tw）

        # =========================
        # 狀態
        # =========================
        self.mods: dict[str, list] = {}
        self.current_modid: str | None = None

        # 快取（防止重複掃描 JAR）
        self._entries_cache: list | None = None   # 緩存的 entries（dict 格式）
        self._cache_meta: dict = {}              # source_root, mode

        self._current_zh_file: Path | None = None
        self._zh_data: dict[str, str] = {}

        # =========================
        # 即時搜尋（Phase 2）
        # =========================
        self._mod_search_text: str = ""
        self._mod_search_debounce_timer: threading.Timer | None = None

        self._detail_search_text: str = ""
        self._detail_search_debounce_timer: threading.Timer | None = None
        self._detail_filtered_entries: list | None = None  # None 表示無搜尋，顯示全部

        # =========================
        # Folder Picker
        # =========================
        self.source_picker = ft.FilePicker(on_result=self._on_pick_source)
        self.review_picker = ft.FilePicker(on_result=self._on_pick_review)
        self.page.overlay.extend([self.source_picker, self.review_picker])

        # ===== 分頁設定 =====
        self.page_size = 50
        self.current_page = 0
        self.total_pages = 0

        # 設定頁數
        self.page_info = ft.Text("")

        self.prev_page_btn = ft.IconButton(
            icon=ft.Icons.CHEVRON_LEFT,
            tooltip="上一頁",
            on_click=self._prev_page,
        )

        self.next_page_btn = ft.IconButton(
            icon=ft.Icons.CHEVRON_RIGHT,
            tooltip="下一頁",
            on_click=self._next_page,
        )

        self.page_bar = ft.Row(
            alignment=ft.MainAxisAlignment.CENTER,
            controls=[
                self.prev_page_btn,
                self.page_info,
                self.next_page_btn,
            ],
        )
        # 每頁顯示數量選擇器（PR61 Issue 1）
        self.page_size_selector = ft.Dropdown(
            label="每頁顯示",
            options=[
                ft.dropdown.Option("25", "25"),
                ft.dropdown.Option("50", "50"),
                ft.dropdown.Option("100", "100"),
            ],
            value="50",
            width=120,
            on_change=self._on_page_size_change,
        )
        # ===== 模組清單分頁 =====
        self.mod_page_size = 50
        self.mod_current_page = 0
        self.mod_total_pages = 0

        # ===== 模組搜尋分頁狀態（PR61 Issue 1） =====
        self._mod_search_matched: list[str] = []   # 目前搜尋結果（所有 matched modid）
        self._mod_search_page: int = 0             # 目前搜尋結果頁碼
        self._mod_search_total: int = 0           # 搜尋結果總頁數

        # =========================
        # UI 元件
        # =========================
        self.header = ft.Text("🧩 JAR 圖示預覽", size=20, weight=ft.FontWeight.BOLD)

        # Mod 清單搜尋框（Phase 2）
        self.mod_search_tf = ft.TextField(
            label="搜尋模組",
            hint_text="輸入 modid（大小寫不敏感）",
            dense=True,
            on_change=self._on_mod_search_change,
            visible=False,
        )
        self.mod_search_status = ft.Text("", size=11, color=theme.GREY_600)

        self.back_btn = ft.IconButton(
            icon=ft.Icons.ARROW_BACK,
            visible=False,
            tooltip="返回模組清單",
            on_click=self._go_back,
        )

        self.pick_source_btn = ft.ElevatedButton(
            "選擇模組資料夾（例：mods 資料夾）",
            icon=ft.Icons.FOLDER_OPEN,
            on_click=lambda e: self.source_picker.get_directory_path(),
        )

        self.pick_review_btn = ft.ElevatedButton(
            "選擇資源包路徑",
            icon=ft.Icons.FOLDER_OPEN,
            on_click=lambda e: self.review_picker.get_directory_path(),
        )

        self.source_label = ft.Text("模組資料夾：尚未選擇", size=12)
        self.review_label = ft.Text("資源包路徑：尚未選擇", size=12)

        self.load_btn = ft.ElevatedButton(
            "載入模組清單",
            icon=ft.Icons.PLAY_ARROW,
            disabled=True,
            on_click=self._on_load_clicked,
        )

        self.save_btn = ft.ElevatedButton(
            "💾 儲存翻譯",
            icon=ft.Icons.SAVE,
            visible=False,
            on_click=self._save_current_zh,
        )

        self.list_view = ft.ListView(expand=True, spacing=8)

        # 進度條
        self.progress_bar = ft.ProgressBar(visible=False, width=500)
        self.progress_text = ft.Text("準備就緒", size=12, color=theme.GREY_600)

        self.controls = [
            ft.Row([self.back_btn, self.header], alignment=ft.MainAxisAlignment.START),
            # Mod 清單搜尋（Phase 2）：搜尋框 + 狀態文字
            self.mod_search_tf,
            self.mod_search_status,
            self.pick_source_btn,
            self.source_label,
            self.pick_review_btn,
            self.review_label,
            self.load_btn,
            # 進度條：置於「載入模組清單」按鈕下方，掃描時才顯示
            self.progress_bar,
            self.progress_text,
            self.save_btn,
            self.page_bar,
            self.page_size_selector,
            ft.Divider(),
            self.list_view,
        ]

    # ==================================================
    # Folder picker callbacks
    # ==================================================
    def _on_pick_source(self, e: ft.FilePickerResultEvent):
        """處理來源目錄選擇結果"""
        if e.path:
            self.source_root = Path(e.path)
            self.source_label.value = f"模組資料夾：{self.source_root}"
            # Phase 3: 向後相容搬移舊 icon cache
            migrated = _migrate_old_icon_cache(self.source_root)
            if migrated:
                self._show_snack("🔄 已搬移舊 icon cache 至新路徑", color=theme.BLUE_600)
            # 快取失效：source_root 改變
            self._entries_cache = None
            self._cache_meta = {}
            self._update_load_state()
            log_info(f"[IconPreview] 模組資料夾已設定: {self.source_root}")
            self._show_snack("✅ 模組資料夾已設定", color=theme.GREEN_600)
        else:
            log_warning("[IconPreview] 模組資料夾選擇已取消")
            self._show_snack("⚠️ 模組資料夾選擇已取消", color=theme.WARNING)

    def _on_pick_review(self, e: ft.FilePickerResultEvent):
        """處理校對目錄選擇結果"""
        if e.path:
            self.review_root = Path(e.path)
            self.review_label.value = f"資源包路徑：{self.review_root}"
            self._update_load_state()
            log_info(f"[IconPreview] 資源包路徑已設定: {self.review_root}")
            self._show_snack("✅ 資源包路徑已設定", color=theme.GREEN_600)
        else:
            log_warning("[IconPreview] 資源包路徑選擇已取消")
            self._show_snack("⚠️ 資源包路徑選擇已取消", color=theme.WARNING)

    def _update_load_state(self):
        """更新載入按鈕的啟用狀態"""
        self.load_btn.disabled = not (self.source_root and self.review_root)
        self.update()

    # ==================================================
    # 載入 → 建立模組清單
    # ==================================================
    def _on_load_clicked(self, e):
        """處理載入按鈕點擊事件"""
        log_info("[IconPreview] 開始掃描模組...")
        self._show_snack("⏳ 掃描模組中...", color=theme.BLUE_600)
        # PR61 Issue 1：載入新模組時清除搜尋狀態
        self._mod_search_matched = []
        self._mod_search_page = 0
        self._mod_search_total = 0
        self._mod_search_text = ""
        self.update()

        mode = self._detect_source_mode()
        log_info(f"[IconPreview] 偵測到模式: {mode}")

        # === 快取檢查（L1 in-memory）===
        cache_valid = (
            self._entries_cache is not None
            and self._cache_meta.get("source_root") == str(self.source_root)
            and self._cache_meta.get("mode") == mode
        )

        if cache_valid:
            log_info("[IconPreview] 使用 L1 快取！")
            self._show_snack(f"✅ 使用快取（共 {len(self._entries_cache)} 筆）", color=theme.GREEN_600)
            # 用快取重建 mods dict（dict 轉回 SimpleNamespace，保持屬性存取相容）
            mods = defaultdict(list)
            for entry in self._entries_cache:
                if isinstance(entry, dict):
                    mods[entry["modid"]].append(SimpleNamespace(**entry))
                else:
                    mods[entry.modid].append(entry)
            self.mods = dict(mods)
            self._render_mod_list()
            return

        # === L2 磁碟快取檢查（只在 jar_directory 模式）===
        if mode == "jar_directory":
            cached_entries = _load_entries_cache_l2(self.source_root)
            if cached_entries is not None:
                log_info("[IconPreview] 使用 L2 磁碟快取！")
                self._show_snack(f"✅ 使用磁碟快取（共 {len(cached_entries)} 筆）", color=theme.GREEN_600)
                self._entries_cache = cached_entries
                self._cache_meta = {
                    "source_root": str(self.source_root),
                    "mode": mode,
                }
                # 重建 mods dict
                mods = defaultdict(list)
                for entry in cached_entries:
                    if isinstance(entry, dict):
                        mods[entry["modid"]].append(SimpleNamespace(**entry))
                    else:
                        mods[entry.modid].append(entry)
                self.mods = dict(mods)
                self._render_mod_list()
                return

        # === 快取 miss ===
        
        # 顯示進度條
        if mode == "jar_directory":
            jar_files = list(self.source_root.glob("*.jar"))
            total_steps = len(jar_files)
        elif mode == "extracted_folder":
            en_files = list(self.source_root.rglob("en_us.json"))
            total_steps = len(en_files)
        else:
            total_steps = 0

        if total_steps > 0:
            self.progress_bar.visible = True
            self.progress_bar.value = 0
            self.progress_text.value = f"正在掃描：0 / {total_steps}"
            self.update()

        processed = 0

        if mode == "jar_directory":
            log_info("[IconPreview] 使用 JAR 目錄模式掃描")
            self._show_snack("📦 JAR 目錄模式：從 JAR 讀取 en_us.json...", color=theme.BLUE_600)
            jar_files = list(self.source_root.glob("*.jar"))
            total_steps = len(jar_files)
            # Phase 3/3：實際讀取翻譯
            entries = self._load_entries_from_jar_directory(
                processed_callback=_make_progress_callback(self, "讀取翻譯內容", total_steps)
            )
        elif mode == "extracted_folder":
            log_info("[IconPreview] 使用解包資料夾模式掃描")
            entries = self._load_entries()
        else:
            log_warning("[IconPreview] 無法識別資料夾模式，或資料夾為空")
            self._show_snack("❌ 無法識別模式，請確認資料夾內容", color=theme.RED_700)
            entries = []

        if not entries:
            log_warning("[IconPreview] 掃描結果為空，確認 en_us.json 是否存在")
            self._show_snack("❌ 掃描結果為空，請確認 en_us.json 是否存在", color=theme.RED_700)
            return

        # 寫入快取（dict 格式，脫離 SimpleNamespace）
        cache_entries = []
        for entry in entries:
            if hasattr(entry, "__dict__"):
                cache_entries.append(entry.__dict__)
            else:
                cache_entries.append(entry)
        self._entries_cache = cache_entries
        self._cache_meta = {
            "source_root": str(self.source_root),
            "mode": mode,
        }

        mods = defaultdict(list)
        for entry in entries:
            mods[entry.modid].append(entry)

        self.mods = dict(mods)
        log_info(f"[IconPreview] 載入完成，共 {len(self.mods)} 個模組，{len(entries)} 筆翻譯")
        self._show_snack(f"✅ 載入完成（共 {len(self.mods)} 個模組）", color=theme.GREEN_600)
        
        # 隱藏進度條
        self.progress_bar.visible = False
        self.progress_text.value = "準備就緒"
        self.update()
        
        self._render_mod_list()

    def _update_progress(self, current: int, total: int):
        """更新進度條"""
        if total > 0:
            self.progress_bar.value = current / total
            self.progress_text.value = f"正在掃描：{current} / {total}"
            self.update()

    def _render_mod_list(self):
        """渲染模組清單畫面"""
        # PR61 Issue 1：清除搜尋結果狀態
        self._mod_search_matched = []
        self._mod_search_page = 0
        self._mod_search_total = 0

        self.current_modid = None
        self.back_btn.visible = False
        self.save_btn.visible = False
        self.header.value = "🧩 JAR 圖示預覽"

        # Phase 2: 顯示 mod 清單搜尋框
        self.mod_search_tf.visible = True
        self.mod_search_status.visible = True
        # 確保 detail 搜尋框隱藏
        if hasattr(self, "detail_search_tf"):
            self.detail_search_tf.visible = False
            self.detail_search_status.visible = False

        mod_ids = sorted(self.mods.keys())
        total = len(mod_ids)

        self.mod_total_pages = max(
            1, (total + self.mod_page_size - 1) // self.mod_page_size
        )

        start = self.mod_current_page * self.mod_page_size
        end = start + self.mod_page_size
        visible_mods = mod_ids[start:end]

        self.list_view.controls.clear()

        for modid in visible_mods:
            entries = self.mods[modid]
            total_count = len(entries)
            untranslated = sum(1 for e in entries if not e.zh_tw.strip())

            self.list_view.controls.append(
                ft.ListTile(
                    title=ft.Text(modid, weight=ft.FontWeight.BOLD),
                    subtitle=ft.Text(f"總數 {total_count} ｜ 未翻譯 {untranslated}"),
                    trailing=ft.Icon(ft.Icons.CHEVRON_RIGHT),
                    on_click=lambda e, m=modid: self._open_mod_detail(m),
                )
            )

        self._update_page_bar_for_mods()
        self.update()

    def _update_page_bar_for_mods(self):
        """更新分頁資訊顯示（同時支援一般清單與搜尋結果分頁）"""
        if self._mod_search_matched:
            # 搜尋結果分頁模式（PR61 Issue 1）
            self.page_info.value = (
                f"搜尋結果｜第 {self._mod_search_page + 1} / {self._mod_search_total} 頁"
            )
            self.prev_page_btn.disabled = self._mod_search_page <= 0
            self.next_page_btn.disabled = self._mod_search_page >= self._mod_search_total - 1
        else:
            # 一般模組清單分頁模式
            self.page_info.value = (
                f"模組清單｜第 {self.mod_current_page + 1} / {self.mod_total_pages} 頁"
            )
            self.prev_page_btn.disabled = self.mod_current_page <= 0
            self.next_page_btn.disabled = self.mod_current_page >= self.mod_total_pages - 1

    def _on_page_size_change(self, e: ft.ControlEvent):
        """處理每頁顯示數量變更（PR61 Issue 1）"""
        self.mod_page_size = int(e.control.value)
        self.mod_current_page = 0  # 重設回第一頁
        self._mod_search_page = 0  # 搜尋結果也重設
        if self._mod_search_matched:
            # 重新渲染搜尋結果的目前頁
            self._render_mod_search_page()
        else:
            self._render_mod_list()

    def _prev_page(self, e):
        """處理上一頁按鈕點擊"""
        if self.current_modid:
            # 第二層（item）
            if self.current_page > 0:
                self.current_page -= 1
                self._render_current_page()
        else:
            # 第一層（模組）
            if self._mod_search_matched:
                # 搜尋結果分頁模式（PR61 Issue 1）
                if self._mod_search_page > 0:
                    self._mod_search_page -= 1
                    self._render_mod_search_page()
            else:
                if self.mod_current_page > 0:
                    self.mod_current_page -= 1
                    self._render_mod_list()

    def _next_page(self, e):
        """處理下一頁按鈕點擊"""
        if self.current_modid:
            if self.current_page < self.total_pages - 1:
                self.current_page += 1
                self._render_current_page()
        else:
            if self._mod_search_matched:
                # 搜尋結果分頁模式（PR61 Issue 1）
                if self._mod_search_page < self._mod_search_total - 1:
                    self._mod_search_page += 1
                    self._render_mod_search_page()
            else:
                if self.mod_current_page < self.mod_total_pages - 1:
                    self.mod_current_page += 1
                    self._render_mod_list()

    # ==================================================
    # 即時搜尋（Phase 2）：Debounce 輔助
    # ==================================================
    def _cancel_mod_search_debounce(self):
        """取消之前的 mod 搜尋 debounce timer"""
        if self._mod_search_debounce_timer:
            self._mod_search_debounce_timer.cancel()
            self._mod_search_debounce_timer = None

    def _cancel_detail_search_debounce(self):
        """取消之前的 detail 搜尋 debounce timer"""
        if self._detail_search_debounce_timer:
            self._detail_search_debounce_timer.cancel()
            self._detail_search_debounce_timer = None

    def _on_mod_search_change(self, e: ft.ControlEvent):
        """Mod 清單搜尋輸入 on_change（debounce 150ms）"""
        self._mod_search_text = e.control.value or ""
        self._cancel_mod_search_debounce()
        self._mod_search_debounce_timer = threading.Timer(0.150, self._do_mod_search)
        self._mod_search_debounce_timer.start()

    def _do_mod_search(self):
        """實際執行 mod 清單搜尋（在 debounce 延遲後執行）"""
        keyword = self._mod_search_text.strip().lower()
        if not keyword:
            self.mod_search_status.value = ""
            self._mod_search_matched = []  # 清除搜尋結果
            self._mod_search_page = 0
            self._mod_search_total = 0
            # 恢復模組清單搜尋框
            self.mod_search_tf.visible = True
            self._render_mod_list()
            return

        all_modids = sorted(self.mods.keys())
        matched = [m for m in all_modids if keyword in m.lower()]
        total = len(all_modids)

        if not matched:
            self.mod_search_status.value = "無符合結果"
            self._mod_search_matched = []
            self._mod_search_page = 0
            self._mod_search_total = 0
            self.list_view.controls.clear()
            self.list_view.controls.append(
                ft.ListTile(
                    title=ft.Text("無符合結果", color=theme.GREY_600),
                    subtitle=ft.Text(f"嘗試不同的關鍵字"),
                )
            )
            self.page_info.value = ""
            self.mod_current_page = 0
        else:
            # PR61 Issue 1：儲存搜尋結果並計算分頁
            self._mod_search_matched = matched
            self._mod_search_page = 0
            self._mod_search_total = max(1, (len(matched) + self.mod_page_size - 1) // self.mod_page_size)
            self.mod_search_status.value = f"符合 {len(matched)} / {total} 個模組"
            self._render_mod_search_page()

        self.update()

    def _render_mod_search_page(self):
        """渲染搜尋結果的目前頁（PR61 Issue 1）"""
        matched = self._mod_search_matched
        total = len(matched)

        start = self._mod_search_page * self.mod_page_size
        end = start + self.mod_page_size
        visible_mods = matched[start:end]

        self.list_view.controls.clear()
        for modid in visible_mods:
            entries = self.mods[modid]
            total_count = len(entries)
            untranslated = sum(1 for e in entries if not e.zh_tw.strip())
            self.list_view.controls.append(
                ft.ListTile(
                    title=ft.Text(modid, weight=ft.FontWeight.BOLD),
                    subtitle=ft.Text(f"總數 {total_count} ｜ 未翻譯 {untranslated}"),
                    trailing=ft.Icon(ft.Icons.CHEVRON_RIGHT),
                    on_click=lambda e, m=modid: self._open_mod_detail(m),
                )
            )

        self._update_page_bar_for_mods()
        self.update()

    def _on_detail_search_change(self, e: ft.ControlEvent):
        """Mod 詳情頁搜尋 on_change（debounce 150ms）"""
        self._detail_search_text = e.control.value or ""
        self._cancel_detail_search_debounce()
        self._detail_search_debounce_timer = threading.Timer(0.150, self._do_detail_search)
        self._detail_search_debounce_timer.start()

    def _do_detail_search(self):
        """實際執行 detail 搜尋（在 debounce 延遲後執行）"""
        keyword = self._detail_search_text.strip().lower()
        entries = self.mods.get(self.current_modid, [])
        total = len(entries)

        if not keyword:
            self._detail_filtered_entries = None  # 無篩選，顯示全部
            if hasattr(self, "detail_search_status"):
                self.detail_search_status.value = ""
        else:
            filtered = [
                e for e in entries
                if keyword in e.key.lower() or keyword in (e.en or "").lower() or keyword in (e.zh_tw or "").lower()
            ]
            self._detail_filtered_entries = filtered
            if hasattr(self, "detail_search_status"):
                self.detail_search_status.value = f"符合 {len(filtered)} / {total} 筆"
            if not filtered:
                if hasattr(self, "detail_search_status"):
                    self.detail_search_status.value = f"無符合結果（{total} 筆）"

        # 重設到第一頁再渲染
        self.current_page = 0
        self._render_current_page()

    def _update_detail_search_controls(self, visible: bool):
        """切換 detail 搜尋 UI 的顯示/隱藏"""
        self._init_detail_search_widgets()
        self.detail_search_tf.visible = visible
        self.detail_search_status.visible = visible

        # 從 controls 中移除再重新加入（確保順序正確：搜尋框在最上方）
        self.controls = [c for c in self.controls if c not in [self.detail_search_tf, self.detail_search_status]]
        if visible:
            idx = self.controls.index(self.list_view) if self.list_view in self.controls else len(self.controls)
            self.controls.insert(idx, self.detail_search_tf)
            self.controls.insert(idx + 1, self.detail_search_status)
        self.update()

    # ==================================================
    # 第二層：單一模組 detail
    # ==================================================

    # Mod 詳情頁搜尋框（Phase 2）- 初始化於 __init__
    def _init_detail_search_widgets(self):
        """初始化 Mod 詳情頁的搜尋 UI（只在需要時建立）"""
        if not hasattr(self, "detail_search_tf"):
            self.detail_search_tf = ft.TextField(
                label="搜尋 key + value",
                hint_text="搜尋 key + value",
                dense=True,
                on_change=self._on_detail_search_change,
                visible=False,
            )
            self.detail_search_status = ft.Text("", size=11, color=theme.GREY_600)

    def _open_mod_detail(self, modid: str):
        """開啟模組詳情畫面"""
        self.current_modid = modid
        self.current_page = 0  # ⭐ 重設頁碼
        self._detail_search_text = ""  # 重設 detail 搜尋
        self._detail_filtered_entries = None  # None = 無篩選，顯示全部
        self.back_btn.visible = True
        self.save_btn.visible = True
        self.header.value = f"📦 {modid}"

        # ===== Phase 2：顯示 Mod 詳情頁搜尋框 =====
        self._init_detail_search_widgets()
        self.detail_search_tf.value = ""
        self.detail_search_status.value = ""

        # 更新 controls：將 detail 搜尋元件插入 list_view 前
        self._update_detail_search_controls(visible=True)

        # ===== Phase 2：隱藏模組清單搜尋框，避免在 Detail View 中誤觸 =====
        self.mod_search_tf.visible = False
        self.mod_search_status.visible = False

        log_info(f"[IconPreview] 開啟模組詳情: {modid}")

        # Track 1：直接路徑（快速）
        direct = self.review_root / modid / "lang" / "zh_tw.json"
        if direct.exists():
            self._current_zh_file = direct
            self._zh_data = load_json_auto_encoding(direct) or {}
            log_info(f"[IconPreview] 直接路徑: {direct}")
        else:
            # Track 2：rglob fallback（容錯）
            zh_files = list(self.review_root.rglob(f"{modid}/lang/zh_tw.json"))
            self._current_zh_file = zh_files[0] if zh_files else None
            if self._current_zh_file and self._current_zh_file.exists():
                self._zh_data = load_json_auto_encoding(self._current_zh_file) or {}
                log_info(f"[IconPreview] rglob fallback: {self._current_zh_file}")
            else:
                self._zh_data = {}
                log_warning(f"[IconPreview] 找不到 zh_tw.json for mod: {modid}")

        self._render_current_page()

    def _go_back(self, e):
        """處理返回按鈕，返回模組清單"""
        self._cancel_detail_search_debounce()  # P1 fix: 取消 pending debounce timer，避免返回後覆蓋列表
        self.current_modid = None
        self.current_page = 0
        self.page_info.value = ""
        self._detail_search_text = ""
        self._detail_filtered_entries = None
        # 隱藏 detail 搜尋 UI
        self._update_detail_search_controls(visible=False)
        self.list_view.controls.clear()

        # ===== Phase 2：恢復模組清單搜尋框 =====
        if hasattr(self, "mod_search_tf"):
            self.mod_search_tf.visible = True
        if hasattr(self, "_mod_search_text") and self._mod_search_text:
            # 如果之前有搜尋文字，重新執行搜尋以顯示過濾後的結果
            self._do_mod_search()
        else:
            # PR61 Issue 1：清除搜尋結果狀態
            if hasattr(self, "_mod_search_matched"):
                self._mod_search_matched = []
            if hasattr(self, "_mod_search_page"):
                self._mod_search_page = 0
            if hasattr(self, "_mod_search_total"):
                self._mod_search_total = 0
            if hasattr(self, "mod_search_status"):
                self.mod_search_status.visible = False
            self._render_mod_list()

    # ==================================================
    # Row → 回報翻譯變更
    # ==================================================
    def _on_value_changed(self, key: str, value: str):
        """處理翻譯值變更事件"""
        self._zh_data[key] = to_halfwidth(value)

    # ==================================================
    # 儲存 zh_tw.json
    # ==================================================
    def _save_current_zh(self, e):
        """儲存目前的翻譯到 zh_tw.json"""
        log_info(f"[IconPreview] 開始儲存翻譯: {self._current_zh_file}")
        self._show_snack("💾 儲存翻譯中...", color=theme.BLUE_600)
        self.update()

        if not self._current_zh_file:
            log_error(f"[IconPreview] 儲存失敗：找不到 zh_tw.json (modid={self.current_modid})")
            self._show_snack("❌ 找不到 zh_tw.json", color=theme.RED_700)
            return

        try:
            self._current_zh_file.parent.mkdir(parents=True, exist_ok=True)
            self._current_zh_file.write_text(
                json.dumps(self._zh_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            log_info(f"[IconPreview] 儲存成功：{self._current_zh_file} ({len(self._zh_data)} 筆翻譯)")
            self._show_snack(f"✅ 翻譯已儲存 ({len(self._zh_data)} 筆)", color=theme.GREEN_600)
        except Exception as ex:
            log_error(f"[IconPreview] 儲存失敗：{ex}")
            self._show_snack(f"❌ 儲存失敗：{ex}", color=theme.RED_700)

    # ==================================================
    # 輔助：SnackBar
    # ==================================================
    def _show_snack(
        self,
        message: str,
        color: str = theme.GREEN_600,
    ):
        """
        統一 SnackBar 顯示（Flet Desktop 穩定版）
        - 使用 page.overlay
        - 不會被 ListView / update 吃掉
        """
        log_info(f"[UI] SnackBar: {message}")
        # 清除累積的舊 SnackBar，避免 overlay 無限膨脹
        # Flet 0.28.3 的 page.overlay 是唯讀屬性（無 setter），需 in-place 修改
        for i in range(len(self.page.overlay) - 1, -1, -1):
            if isinstance(self.page.overlay[i], ft.SnackBar):
                del self.page.overlay[i]
        snack = ft.SnackBar(
            content=ft.Text(message),
            bgcolor=color,
            duration=3000,
        )

        # ⚠️ 關鍵：一定要加在 overlay
        self.page.overlay.append(snack)

        snack.open = True
        self.page.update()

    # ==================================================
    # 核心資料載入（只處理 JSON）
    # ==================================================
    def _load_entries(self):
        """
        - 以 source_root 的 en_us.json 為主
        - 對照 review_root 的 zh_tw.json
        - 只建立索引，不處理 icon
        """
        entries = []

        if not self.source_root or not self.review_root:
            return entries

        # 改成雙軌
        # 方式 A：直接路徑（需先知道所有 modid）
        # 先從 source_root 掃出 modid 清單
        modid_set = set()
        for en_file in self.source_root.rglob("en_us.json"):
            parts = en_file.parts
            try:
                idx = parts.index("assets")
                modid = parts[idx + 1]
                modid_set.add(modid)
            except (ValueError, IndexError):
                continue

        # Track 1：直接路徑（快速）
        zh_map = {}
        for modid in modid_set:
            direct = self.review_root / modid / "lang" / "zh_tw.json"
            if direct.exists():
                data = load_json_auto_encoding(direct)
                if isinstance(data, dict):
                    zh_map.update(data)
                    log_info(f"[IconPreview] 雙軌-直接: {direct}")

        # Track 2：rglob fallback（容錯，找漏網）
        found_paths = set(str(direct) for modid in modid_set for direct in [self.review_root / modid / "lang" / "zh_tw.json"] if direct.exists())
        for zh_file in self.review_root.rglob("zh_tw.json"):
            if str(zh_file) not in found_paths:
                data = load_json_auto_encoding(zh_file)
                if isinstance(data, dict):
                    zh_map.update(data)
                    log_warning(f"[IconPreview] 雙軌-rglob補漏: {zh_file}")

        # 掃描 en_us
        for en_file in self.source_root.rglob("en_us.json"):
            data = load_json_auto_encoding(en_file)
            if not isinstance(data, dict):
                continue

            try:
                parts = en_file.parts
                idx = parts.index("assets")
                modid = parts[idx + 1]
            except Exception:
                modid = "unknown"

            for key, en_text in data.items():
                zh_tw_raw = zh_map.get(key, "")
                if not isinstance(zh_tw_raw, str):
                    zh_tw_raw = ""
                entries.append(
                    SimpleNamespace(
                        modid=modid,
                        key=key,
                        en=en_text,
                        zh_tw=zh_tw_raw.strip(),
                    )
                )

        return entries

    # ==================================================
    # JAR 目錄模式：偵測與掃描
    # ==================================================
    def _detect_source_mode(self) -> str:
        """偵測 source_root 是「JAR 目錄」還是「已解包資料夾」。

        回傳：
            "jar_directory"   - mods 資料夾模式（JAR 檔案優先）
            "extracted_folder" - 傳統解包資料夾（en_us.json 存在）
            "empty"           - 無內容或無法識別
        """
        if not self.source_root:
            return "unknown"

        jar_count = len(list(self.source_root.glob("*.jar")))
        extracted_count = len(list(self.source_root.rglob("en_us.json")))

        if jar_count > 0 and extracted_count == 0:
            log_info(f"[IconPreview] 偵測為 JAR 目錄模式（{jar_count} 個 JAR 檔）")
            return "jar_directory"
        elif extracted_count > 0:
            log_info(f"[IconPreview] 偵測為解包資料夾模式（{extracted_count} 個 en_us.json）")
            return "extracted_folder"
        else:
            log_warning(f"[IconPreview] 無法識別模式：JAR={jar_count}, en_us={extracted_count}")
            return "empty"

    def _load_entries_from_jar_directory(self, processed_callback=None) -> list:
        """從 JAR 目錄讀取所有 en_us.json（不改磁碟，直接讀 ZIP 內容）。

        流程：
            1. Phase 1/3：收集 modid 清單
            2. Phase 2/3：建立 zh_tw 對照表（雙軌制）
            3. Phase 3/3：建立 entries
        """
        if self.source_root is None:
            return []
        jar_files = list(self.source_root.glob("*.jar"))
        total_steps = len(jar_files)
        failed_jars = []

        # ===== Phase 1/3：收集所有 modid =====
        _show_progress_phase(self, "收集模組資訊", 0, total_steps)
        all_modids = set()
        for jar_path in jar_files:
            try:
                with zipfile.ZipFile(jar_path, 'r') as zf:
                    for name in zf.namelist():
                        if not name.endswith("lang/en_us.json"):
                            continue
                        parts = name.split('/')
                        if len(parts) < 3 or parts[-2] != 'lang' or parts[-1] != 'en_us.json':
                            continue
                        modid = parts[1]
                        all_modids.add(modid)
            except Exception:
                pass
        _show_progress_phase(self, "收集模組資訊", total_steps, total_steps)

        # ===== Phase 2/3：建立 zh_tw 對照表（雙軌制）=====
        _show_progress_phase(self, "建立翻譯對照表", 0, 1)
        zh_map = {}
        if self.review_root and all_modids:
            # Track 1：直接路徑
            for modid in all_modids:
                direct = self.review_root / modid / "lang" / "zh_tw.json"
                if direct.exists():
                    data = load_json_auto_encoding(direct)
                    if isinstance(data, dict):
                        zh_map.update(data)
                        log_info(f"[IconPreview] JAR雙軌-直接: {direct}")

            # Track 2：rglob fallback
            found_paths = set(str(self.review_root / modid / "lang" / "zh_tw.json") for modid in all_modids)
            for zh_file in self.review_root.rglob("zh_tw.json"):
                if str(zh_file) not in found_paths:
                    data = load_json_auto_encoding(zh_file)
                    if isinstance(data, dict):
                        zh_map.update(data)
                        log_warning(f"[IconPreview] JAR雙軌-rglob補漏: {zh_file}")

            log_info(f"[IconPreview] 已建立 zh_tw 對照表，共 {len(zh_map)} 筆")
        _show_progress_phase(self, "建立翻譯對照表", 1, 1)

        # ===== Phase 3/3：使用 jar_browser 多執行緒掃描 =====
        entries = []
        failed_jars = []

        # 墊底一次：讓使用者知道掃描啟動了
        # 向後兼容：舊測試使用 0 參數 callback，新設計使用 2 參數 callback
        if processed_callback:
            try:
                processed_callback(0, total_steps)
            except TypeError:
                try:
                    processed_callback()
                except TypeError:
                    pass  # 忽略不兼容的 callback

        from translation_tool.utils.jar_browser import scan_jars

        # 包裝 callback：同時支援 0 參數（旧測試）和 2 參數（新設計）
        def wrapped_callback(processed: int, total: int):
            if processed_callback:
                try:
                    processed_callback(processed, total)
                except TypeError:
                    try:
                        processed_callback()
                    except TypeError:
                        pass

        results = scan_jars(
            jar_dir=self.source_root,
            patterns=[r"assets/([^/]+)/lang/en_us\.json"],
            processed_callback=wrapped_callback,
        )

        # 建立 entries
        for jar_path, files in results.items():
            for name, content in files.items():
                if not name.endswith("lang/en_us.json"):
                    continue
                if content is None:
                    continue  # binary 檔案（不應該在這裡出現）

                parts = name.split("/")
                modid = parts[1]

                try:
                    data = json.loads(content)
                except json.JSONDecodeError:
                    log_warning(f"[IconPreview] JAR 解析 JSON 失敗: {jar_path.name} / {name}")
                    failed_jars.append(jar_path.name)
                    continue

                if not isinstance(data, dict):
                    continue

                jar_entries_count = 0
                for key, en_text in data.items():
                    zh_tw_raw = zh_map.get(key, "")
                    if not isinstance(zh_tw_raw, str):
                        zh_tw_raw = ""
                    entries.append(SimpleNamespace(
                        modid=modid,
                        key=key,
                        en=en_text,
                        zh_tw=zh_tw_raw.strip(),
                        source_jar=jar_path.name,
                    ))
                    jar_entries_count += 1

                log_info(f"[IconPreview] {jar_path.name}: 找到 {jar_entries_count} 筆翻譯")

        log_info(f"[IconPreview] JAR 目錄掃描完成：共 {len(entries)} 筆翻譯")

        # ===== JAR Icon 掃描：提取 mod icons =====
        icon_cache_root = _get_icon_cache_dir()

        # ===== Phase 4/4：批次提取模組圖示（每個 JAR 只開一次 ZIP）=====
        if _ENABLE_JAR_ICON:
            # 按 source_jar 分組
            jar_to_entries: dict[str, list] = defaultdict(list)
            for e in entries:
                if getattr(e, "source_jar", None):
                    jar_to_entries[e.source_jar].append(e)

            icon_total = len(jar_to_entries)

            def _on_icon_progress(done: int, total: int):
                _show_progress_phase(self, "提取模組圖示", done, total)

            _batch_extract_jar_icons(jar_to_entries, icon_cache_root, self.source_root, _on_icon_progress)

        # ===== 寫入 L2 磁碟快取 =====
        _save_entries_cache_l2(self.source_root, entries)
        log_info(f"[IconPreview] 已寫入 L2 磁碟快取")

        return entries

    def _render_current_page(self):
        """渲染當前頁面的項目列表（支援 detail 搜尋過濾）"""
        # Phase 2：搜尋過濾邏輯
        if self._detail_filtered_entries is not None:
            # 有搜尋條件，使用過濾後的 entries
            entries = self._detail_filtered_entries
            search_active = True
        else:
            entries = self.mods.get(self.current_modid, [])
            search_active = False

        total = len(entries)

        self.total_pages = max(1, (total + self.page_size - 1) // self.page_size)

        start = self.current_page * self.page_size
        end = start + self.page_size

        self.list_view.controls.clear()

        for entry in entries[start:end]:
            self.list_view.controls.append(
                LangItemRow(
                    lang_key=entry.key,
                    en_text=entry.en,
                    zh_text=self._zh_data.get(entry.key, ""),
                    assets_root=self.source_root / "assets",
                    preview_root=_get_icon_cache_dir(),
                    on_value_changed=self._on_value_changed,
                    icon_path=getattr(entry, "icon_path", None),
                )
            )

        self.page_info.value = (
            f"{self.current_modid}｜第 {self.current_page + 1} / {self.total_pages} 頁"
        )
        self.prev_page_btn.disabled = self.current_page <= 0
        self.next_page_btn.disabled = self.current_page >= self.total_pages - 1

        self.update()
