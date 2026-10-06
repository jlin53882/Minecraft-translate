"""圖示預覽頁的圖示提取與快取輔助函式（由 icon_preview_view.py 拆出，#114）。

含 JAR 內 model JSON 解析、圖示提取與批次處理、model index／L2 entries 快取與進度輔助。
測試要 monkeypatch 這些函式時，請 patch 本模組（呼叫者都在這裡查名稱）。
"""

import json
import re
import shutil
import threading
import unicodedata
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from app.icon_reader import IconRef
from translation_tool.utils.app_paths import get_data_root
from translation_tool.utils.config_manager import load_config
from translation_tool.utils.log_unit import log_info, log_warning
from translation_tool.utils.zip_safety import (
    MAX_ICON_BYTES,
    ArchiveBudgetError,
    ZipReadBudget,
    read_limited,
    safe_join,
)

# ==================================================
# 實驗性功能開關
# ==================================================
_ENABLE_JAR_ICON = True  # 已啟用（Model JSON 解析 + 批次 ZIP icon 提取）

# 真正需要遊戲圖示的 key 前綴（只有這些才 fallback 到 logo.png）
# 不在清單裡的 key（如 _comment、advancements.*、recipe_type、jei.* 等）不該有 icon
_CONTENT_ICON_PREFIXES = frozenset(
    [
        "item",
        "block",
        "entity",
        "enchantment",
        "effect",
        "potion",
        "biome",
        "attribute",
        "tile",
        "-effect",
    ]
)


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
    return get_data_root() / ".icon_cache" / "jar_icons"


def _get_model_index_cache_dir() -> Path:
    """取得 model index 快取目錄（.icon_cache/model_index/）。"""
    return get_data_root() / ".icon_cache" / "model_index"


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

    log_info(
        f"[IconPreview] 已將 {files_moved} 個 icon 檔案從舊路徑搬移至新路徑: {old_path} → {new_path}"
    )
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


# 程序內 model index 快取：(jar 路徑, modid) → (jar_hash, index)
# 同一個 JAR 的每個物品都會查 model index；原本每次都重新讀取並解析 JSON 快取檔
# （40 個 JAR 就讀了 9,179 次），改為每個 JAR 只讀一次。
_MODEL_INDEX_MEMO: dict[tuple[str, str], tuple[str, dict]] = {}
_MODEL_INDEX_MEMO_LOCK = threading.Lock()


def _load_model_index_from_cache(jar_path: Path, modid: str) -> dict | None:
    """嘗試讀取 model index cache（先查程序內快取，再查磁碟）。

    失效條件：JAR 的 mtime/size 改變，或 cache 檔不存在/格式無效。

    回傳：
        model_index dict（name → [路徑列表]），或 None（cache miss）
    """
    memo_key = (str(jar_path), modid)
    current_hash = _get_jar_hash(jar_path)
    with _MODEL_INDEX_MEMO_LOCK:
        memo = _MODEL_INDEX_MEMO.get(memo_key)
    if memo is not None and memo[0] == current_hash:
        return memo[1]

    index = _load_model_index_from_disk(jar_path, modid, current_hash)
    if index is not None:
        with _MODEL_INDEX_MEMO_LOCK:
            _MODEL_INDEX_MEMO[memo_key] = (current_hash, index)
    return index


def _load_model_index_from_disk(
    jar_path: Path, modid: str, current_hash: str
) -> dict | None:
    """從磁碟讀取 model index cache；hash 或 modid 不符時回傳 None。"""
    cache_dir = _get_model_index_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe_jar_name = jar_path.stem  # stem 已剝除副檔名
    cache_file = cache_dir / f"{safe_jar_name}.json"

    if not cache_file.exists():
        return None

    try:
        with open(cache_file, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    # 檢查 jar_hash 是否匹配
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
    with _MODEL_INDEX_MEMO_LOCK:
        _MODEL_INDEX_MEMO[(str(jar_path), modid)] = (data["jar_hash"], model_index)


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
        rel = n[len(prefix) :]
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
        return next(iter(textures.values()))

    for key in ["layer0", "front", "particle"]:
        if key in textures:
            return textures[key]

    return next(iter(textures.values()))


def _follow_parent_chain(
    model_path: str,
    names: set[str],
    modid: str,
    zf: zipfile.ZipFile,
    visited: set[str] | None = None,
    budget: ZipReadBudget | None = None,
) -> str | None:
    """沿 parent chain 遞迴向上找，直到找到有 textures 的 model。

    遇到 minecraft: 開頭的 parent 直接跳過（不處理 Minecraft 內建資源）。
    """
    if visited is None:
        visited = set()

    if model_path in visited or model_path not in names:
        return None
    visited.add(model_path)

    try:
        raw = read_limited(zf, model_path, budget=budget).decode(
            "utf-8", errors="replace"
        )
        data = json.loads(raw)
    except ArchiveBudgetError:
        raise  # 整包累計超限：交給呼叫端中止這個 JAR，不當成單一 model 解析失敗
    except Exception:  # noqa: BLE001
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

    return _follow_parent_chain(parent_path, names, modid, zf, visited, budget)


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
    budget: ZipReadBudget | None = None,
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
        rest = key[len(prefix) + 1 + len(modid) + 1 :]  # "restonia_crystal_block"
        model_name = f"{prefix}/{rest}"  # "block/restonia_crystal_block"

        if model_name in model_index:
            for model_path in model_index[model_name]:
                tex_val = _follow_parent_chain(
                    model_path, names, modid, zf, budget=budget
                )
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


def _icon_cache_file(
    icon_cache_root: Path, modid: str, jar_path: Path, key: str
) -> Path:
    """icon 快取檔路徑。modid 來自 JAR / ZIP 內容，不可信：結果必須留在 icon_cache_root 內
    （Windows 上 modid 內含反斜線時會被當成目錄分隔符，safe_join 會拒絕逃逸）。"""
    return Path(
        safe_join(
            icon_cache_root, f"{modid}_{jar_path.stem}_{_safe_filename_key(key)}.png"
        )
    )


def _store_icon(
    zf: zipfile.ZipFile,
    member: str,
    icon_cache_root: Path,
    modid: str,
    jar_path: Path,
    key: str,
    budget: ZipReadBudget,
) -> Path:
    """讀出 JAR 內的圖示檔並寫入圖示快取，回傳快取檔路徑。"""
    icon_data = read_limited(zf, member, MAX_ICON_BYTES, budget=budget)
    icon_cache_root.mkdir(parents=True, exist_ok=True)
    out_path = _icon_cache_file(icon_cache_root, modid, jar_path, key)
    out_path.write_bytes(icon_data)
    return out_path


def _neoforge_logo_member(
    zf: zipfile.ZipFile, names: set[str], budget: ZipReadBudget
) -> str | None:
    """從 NeoForge 的 neoforge.mods.toml 取得 logoFile 在 JAR 內的路徑（找不到回傳 None）。"""
    neoforge_toml = "META-INF/neoforge.mods.toml"
    if neoforge_toml not in names:
        return None
    try:
        toml_content = read_limited(zf, neoforge_toml, budget=budget).decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not toml_content:
        return None
    logo_match = re.search(r'logoFile\s*=\s*"([^"]+\.png)"', toml_content)
    if logo_match and logo_match.group(1) in names:
        return logo_match.group(1)
    return None


def _extract_jar_icon(
    jar_path: Path, modid: str, icon_cache_root: Path, key: str
) -> Path | None:
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
            budget = ZipReadBudget.for_icon_scan(jar_path.name)

            # ===== Phase 1: Model JSON 解析（最高優先）=====
            result = _try_extract_mod_icon_from_model(
                jar_path, modid, zf, names, key=key, budget=budget
            )
            if result:
                tex_val, png_path = result
                out_path = _store_icon(
                    zf, png_path, icon_cache_root, modid, jar_path, key, budget
                )
                log_info(
                    f"[IconPreview] Model JSON icon: {modid} → {png_path} (tex={tex_val})"
                )
                return out_path

            # ===== Fallback: assets/<modid>/icon.png（Fabric 標準）=====
            fabric_icon = f"assets/{modid}/icon.png"
            if fabric_icon in names:
                out_path = _store_icon(
                    zf, fabric_icon, icon_cache_root, modid, jar_path, key, budget
                )
                log_info(f"[IconPreview] 提取 Fabric icon.png: {modid}")
                return out_path

            # ===== Fallback: assets/<modid>/textures/*.png（Fabric glob）=====
            textures_pattern = re.compile(
                r"^assets/" + re.escape(modid) + r"/textures/.+\.png$"
            )
            texture_files = sorted(n for n in names if textures_pattern.match(n))
            if texture_files:
                out_path = _store_icon(
                    zf, texture_files[0], icon_cache_root, modid, jar_path, key, budget
                )
                log_info(
                    f"[IconPreview] 提取 Fabric texture icon: {modid} → {texture_files[0]}"
                )
                return out_path

            # ===== Fallback: assets/<modid>/textures/logo.png =====
            logo_texture = f"assets/{modid}/textures/logo.png"
            if logo_texture in names:
                out_path = _store_icon(
                    zf, logo_texture, icon_cache_root, modid, jar_path, key, budget
                )
                log_info(f"[IconPreview] 提取 logo.png: {modid}")
                return out_path

            # ===== Fallback: NeoForge logoFile =====
            logo_path = _neoforge_logo_member(zf, names, budget)
            if logo_path:
                out_path = _store_icon(
                    zf, logo_path, icon_cache_root, modid, jar_path, key, budget
                )
                log_info(f"[IconPreview] 提取 NeoForge logoFile: {modid} → {logo_path}")
                return out_path

    except Exception as ex:  # noqa: BLE001
        log_warning(
            f"[IconPreview] 提取 JAR icon 失敗: {jar_path.name} / {modid} → {ex!r}"
        )

    return None


# ==================================================
# 批次 Icon 提取（每個 JAR 只開一次 ZIP）
# ==================================================


def _apply_icon_index(jar_to_entries: dict[str, list], icon_index, progress_cb) -> int:
    """索引存在：直接用索引套用 icon，完全不做 JAR I/O。回傳處理的 JAR 數量。"""
    applied = 0
    for entries in jar_to_entries.values():
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


def _run_jar_workers(jar_to_entries: dict[str, list], process_jar, progress_cb) -> int:
    """以 ThreadPoolExecutor 並行處理每個 JAR，並把結果套用到對應的 entry。回傳已處理的 JAR 數量。"""
    processed = 0
    total = len(jar_to_entries)
    config_workers = (
        load_config().get("translator", {}).get("parallel_execution_workers", 4)
    )
    max_workers = max(1, config_workers) if isinstance(config_workers, int) else 4
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(process_jar, jar_name): jar_name
            for jar_name in jar_to_entries
        }
        for future in as_completed(futures):
            jar_name = futures[future]
            try:
                entry_icon_paths = future.result()
                for e in jar_to_entries.get(jar_name, []):
                    if hasattr(e, "key") and e.key in entry_icon_paths:
                        uri = entry_icon_paths[e.key]
                        if uri:
                            e.icon_path = uri
            except Exception as exc:  # noqa: BLE001 - 單一 JAR 圖示解析失敗不中止整批，但要留下是哪個 JAR
                log_warning(
                    f"[IconPreview] {jar_name} 圖示解析失敗：{exc!r}", exc_info=True
                )
            processed += 1
            if progress_cb:
                progress_cb(processed, total)
                if processed % 50 == 0:
                    log_info(f"[IconPreview] 處理進度：{processed}/{total} JARs")
    return processed


def _batch_extract_jar_icons(
    jar_to_entries: dict[str, list],
    icon_cache_root: Path,
    source_root: Path,
    progress_cb=None,
) -> int:
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

    # ===== Phase 0: Per-batch in-memory cache：同一 (modid, key) 在同一批次內不重複解析 =====
    _result_cache: dict[tuple[str, str], str | None] = {}

    # ===== Phase 1: 嘗試從預建立索引讀取（instant）=====
    icon_index = None
    try:
        from app import icon_index as idx_module

        icon_index = idx_module.load_icon_index(source_root)
    except Exception as exc:  # noqa: BLE001 - 預建索引載入失敗時改為逐 JAR 解析，但要留下紀錄
        log_warning(f"[IconPreview] 載入預建圖示索引失敗，改為逐 JAR 解析：{exc!r}")

    if icon_index is not None:
        return _apply_icon_index(jar_to_entries, icon_index, progress_cb)

    # ===== Phase 2: 無索引 → ThreadPoolExecutor 即時處理 =====
    log_info(
        f"[IconPreview] 無索引，啟動 ThreadPoolExecutor 處理 {len(jar_to_entries)} 個 JAR"
    )

    def _process_jar(jar_name: str) -> dict[str, str | None]:
        """Worker：處理單一 JAR，回傳 {key: icon_uri or None}。"""
        jar_path = source_root / jar_name
        result_map: dict[str, str | None] = {}
        if not jar_path.exists():
            return result_map
        try:
            with zipfile.ZipFile(jar_path, "r") as zf:
                names = set(zf.namelist())
                budget = ZipReadBudget.for_icon_scan(jar_name)
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
                    res = _try_extract_mod_icon_from_model(
                        jar_path, modid, zf, names, key=key, budget=budget
                    )
                    if res:
                        _tex_val, png_path = res
                        uri = IconRef(jar_path, png_path).to_uri()
                        result_map[key] = uri
                        _result_cache[cache_key] = uri
                    else:
                        _result_cache[cache_key] = None
                        result_map[key] = None
        except ArchiveBudgetError:
            # 累計讀取超過安全上限（budget 已記錄警告）：保留已解析的部分，略過剩餘項目
            log_warning(
                f"[IconPreview] 略過 {jar_name} 剩餘項目的圖示解析（累計讀取超限）"
            )
        except Exception as exc:  # noqa: BLE001 - 單一 JAR 解析失敗保留已解析的部分，但要留下是哪個 JAR
            log_warning(
                f"[IconPreview] {jar_name} 圖示解析中斷：{exc!r}", exc_info=True
            )
        return result_map

    processed = _run_jar_workers(jar_to_entries, _process_jar, progress_cb)

    log_info(f"[IconPreview] ThreadPoolExecutor 完成：{processed} 個 JAR")
    return processed


# ==================================================
# L2 磁碟快取工具函式
# ==================================================


# ==================================================
# Phase 進度條輔助
# ==================================================


def to_halfwidth(text):
    """
    將字串正規化為半形（NFKC）
    - 只處理 str
    - 非 str 原樣返回（安全）
    """
    if not isinstance(text, str):
        return text
    return unicodedata.normalize("NFKC", text)
