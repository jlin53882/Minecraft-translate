"""translation_tool/core/kubejs_translator.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

# kubejs_translator.py
from __future__ import annotations

from pathlib import Path
from typing import Optional, Callable, Dict, Any
import time
import math


# 導入我們自訂的日誌工具
from translation_tool.utils.log_unit import (
    log_info,  
    log_error, 
    log_warning, 
    log_debug, 
    progress, 
    get_formatted_duration
)

import re
from translation_tool.utils.text_processor import safe_convert_text  # 你 FTB 也有用類似概念
import orjson
from translation_tool.core.lm_translator_shared import _get_default_batch_size


# 定義語言引用格式的正規表示式
# 匹配以 '{' 開頭、'}' 結尾，且中間包含至少一個字元的字串 (例如: "{key_name}")
_LANG_REF_RE = re.compile(r"^\{.+\}$")

def _is_filled_text(v: Any) -> bool:
    """
    檢查輸入值是否為有實質內容的文字。
    
    回傳 False 的情況：
    1. 不是字串類型
    2. 是空字串或只包含空白
    3. 格式符合語言引用標記 (例如 "{translation_key}")
    """
    
    # 檢查是否為字串類型，若不是則判定為無實質文字
    if not isinstance(v, str):
        return False
    
    # 去除首尾空白
    s = v.strip()
    
    # 檢查去除空白後是否為空字串
    if not s:
        return False
    
    # 檢查字串是否符合語言引用格式
    # 這通常用於排除尚未翻譯或僅作為佔位符的標記
    if _LANG_REF_RE.match(s):
        return False
    
    # 若通過以上檢查，則判定為有效的實質文字
    return True

def deep_merge_3way_flat(tw: dict, cn: dict, en: dict) -> dict:
    """
    執行三方語系檔的深度合併（扁平化版本）。
    
    優先順序邏輯：
    1. 繁體中文 (zh_tw)：最優先使用。
    2. 簡體中文 (zh_cn)：若無繁中，則將簡中轉為繁中後使用。
    3. 英文 (en_us)：若前兩者皆無，則使用英文作為最後備援。
    
    註：若呼叫時傳入 en={}，則代表不希望使用英文回退，輸出將僅包含中文字串。
    """
    out = {}
    # 聯集所有字典中的 Key，確保每個存在的翻譯鍵值都會被處理到
    keys = set(tw.keys()) | set(cn.keys()) | set(en.keys())
    
    for k in keys:
        # 1. 檢查繁體中文
        v_tw = tw.get(k)
        if _is_filled_text(v_tw):
            out[k] = v_tw
            continue

        # 2. 檢查簡體中文
        v_cn = cn.get(k)
        if _is_filled_text(v_cn):
            # 使用 safe_convert_text 將簡體字轉為繁體字，維持語系一致性
            out[k] = safe_convert_text(v_cn)
            continue

        # 3. 檢查英文 (最後的備援方案)
        v_en = en.get(k)
        if _is_filled_text(v_en):
            out[k] = v_en
            
    return out

def prune_en_by_tw_flat(en_map: dict, tw_available: dict) -> dict:
    """
    根據繁體中文的可用性來「剪裁」英文語系檔。
    
    用途：
    常用於找出「尚未翻譯」的清單。如果一個 Key 在繁體中文已經有內容了，
    就不再需要保留其英文版本（避免重複或用於產生待翻譯任務）。
    """
    out = {}
    for k, v in en_map.items():
        # 如果該 Key 在繁體中文版中已經有有效內容，則跳過（剪裁掉）
        if _is_filled_text(tw_available.get(k)):
            continue
        
        # 只有當繁體中文還沒有內容時，才保留這個英文翻譯
        out[k] = v
        
    return out


def _read_json_dict_orjson(path: Path) -> dict:
    """
    使用 orjson 讀取 JSON 檔案並轉換為字典。
    
    特點：
    1. 自動處理編碼問題（BOM）。
    2. 自動修正結尾逗號（Trailing Comma），避免 orjson 解析失敗。
    3. 異常安全：出錯時回傳空字典。
    """
    # 檢查路徑是否存在且為檔案
    if not path or not path.is_file():
        return {}
        
    try:
        # 讀取文字內容
        raw = path.read_text(encoding="utf-8")
        
        # 移除 UTF-8 BOM 標頭（有些舊 Windows 軟體產生的檔案會帶有此標記）
        raw = raw.lstrip("\ufeff")
        
        # 修正尾逗號問題：
        # 正則表達式匹配：逗號 + 任意空白 + 右大/中括號
        # 例如將 {"a":1,} 轉換為 {"a":1}
        raw = re.sub(r",\s*([}\]])", r"\1", raw)
        
        # orjson.loads 接收 bytes 效能最佳，故轉碼後解析
        data = orjson.loads(raw.encode("utf-8"))
        
        # 確保回傳格式為字典，否則回傳空字典
        return data if isinstance(data, dict) else {}
    except Exception:
        # 發生任何解析或 IO 錯誤時，返回空字典以防程式崩潰
        return {}


def _write_json_orjson(path: Path, data: dict) -> None:
    """
    使用 orjson 將字典寫入 JSON 檔案。

    特點：
    1. 自動建立不存在的父資料夾。
    2. 使用 2 格空格縮排（Pretty Print）。
    3. 會在寫入前將所有 dict key 正規化為字串，確保輸出符合標準 JSON。
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    def _normalize_json_keys(obj: Any) -> Any:
        """遞迴將 dict key 正規化為 str，避免產生非標準 JSON Key。"""
        if isinstance(obj, dict):
            return {str(k): _normalize_json_keys(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_normalize_json_keys(v) for v in obj]
        return obj

    normalized = _normalize_json_keys(data)

    b = orjson.dumps(
        normalized,
        option=orjson.OPT_INDENT_2,  # ✅ 不再使用 OPT_NON_STR_KEYS
    )
    path.write_bytes(b)



def clean_kubejs_from_raw(
    base_dir: str,
    *,
    output_dir: str | None = None,
    raw_dir: str | None = None,
    pending_root: str | None = None,   
    final_root: str | None = None,     
) -> dict:
    """
    KubeJS 語系檔案清理與分流（orjson 版）
    
    工作流程：
    1. 讀取 raw 目錄下的所有 JSON。
    2. 分類：
       - 非 lang 資料夾內的 JSON（如 tooltip, ponder）：視為待處理，直接複製到「待翻譯」。
       - lang 資料夾內的 JSON：進行三方合併與剪裁。
    3. 產出：
       - 「完成」區：產出 zh_tw.json，合併 tw 與 cn（轉繁），不使用英文回退。
       - 「待翻譯」區：產出 en_us.json，僅保留繁中尚未翻譯的項目。
    """
    # 初始化路徑
    base = Path(base_dir).resolve()
    out_root = Path(output_dir).resolve() if output_dir else (base / "Output")

    # 定義原始、待翻譯、完成三個核心路徑
    raw_root = Path(raw_dir).resolve() if raw_dir else (out_root / "kubejs" / "raw" / "kubejs")
    pending_root_p = Path(pending_root).resolve() if pending_root else (out_root / "kubejs" / "待翻譯" / "kubejs")
    final_root_p   = Path(final_root).resolve() if final_root else (out_root / "kubejs" / "完成" / "kubejs")

    # 確保輸出目錄存在
    pending_root_p.mkdir(parents=True, exist_ok=True)
    final_root_p.mkdir(parents=True, exist_ok=True)

    # 1) 分類 raw 內的檔案：區分語系檔 (lang) 與其他資料檔 (tooltip/ponder)
    lang_files = []
    other_jsons = []
    for p in raw_root.rglob("*.json"):
        pp = str(p).replace("\\", "/") # 統一轉為正斜線方便判斷
        if "/lang/" in pp:
            lang_files.append(p)
        else:
            other_jsons.append(p)

    # 2) 非 lang 檔案處理：直接複製到「待翻譯」目錄，維持目錄結構
    copied_other = 0
    for p in other_jsons:
        rel = p.relative_to(raw_root)
        # 注意：這裡應使用實例化的 pending_root_p
        dst = pending_root_p / rel 
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(p.read_bytes())
        copied_other += 1

    # 3) lang 分組：將同一個資料夾下的不同語系檔歸類在一起
    # 格式：{ Path('.../lang'): {'en_us': Path(...), 'zh_cn': Path(...)} }
    groups: dict[Path, dict[str, Path]] = {}
    for p in lang_files:
        group_dir = p.parent
        lang_name = p.stem.lower()
        groups.setdefault(group_dir, {})[lang_name] = p

    merged_lang_written = 0
    pending_lang_written = 0

    # 4) 逐組處理語系合併
    for group_dir, files_map in groups.items():
        # 讀取三方內容
        en = _read_json_dict_orjson(files_map.get("en_us"))
        cn = _read_json_dict_orjson(files_map.get("zh_cn"))
        tw = _read_json_dict_orjson(files_map.get("zh_tw"))

        log_debug(
            f"[KubeJS-CLEAN-DBG] group={group_dir} | en={len(en or {})} cn={len(cn or {})} tw={len(tw or {})}"
        )

        has_twcn = bool(cn or tw)
        rel_group = group_dir.relative_to(raw_root)

        # A) 處理「待翻譯」en_us.json
        if en:
            if has_twcn:
                # 取得目前已有的繁中內容（tw + cn轉繁）
                available_tw = deep_merge_3way_flat(tw, cn, {}) 
                # 從英文檔中剔除已經有繁中內容的 Key
                pending_en = prune_en_by_tw_flat(en, available_tw)
            else:
                # 完全沒有中文字心，則整份英文都是待翻譯
                pending_en = en

            if pending_en: # 只有當還有剩餘未翻譯項時才寫檔
                dst_en = pending_root_p / rel_group / "en_us.json"
                _write_json_orjson(dst_en, pending_en)
                pending_lang_written += 1

        # B) 處理「完成」zh_tw.json
        if has_twcn:
            # 合併繁中與簡中（不使用英文當備援，確保輸出的全是中文）
            merged_tw = deep_merge_3way_flat(tw, cn, {})
            
            dst_tw = final_root_p / rel_group / "zh_tw.json"
            _write_json_orjson(dst_tw, merged_tw)
            merged_lang_written += 1

    # 5) 記錄處理結果
    log_info(
        f"[KubeJS-CLEAN] 處理完畢！群組數: {len(groups)} | 產出待翻譯: {pending_lang_written} | 產出完成品: {merged_lang_written} | 複製其他檔案: {copied_other}"
    )


    return {
        "raw_root": str(raw_root),
        "pending_root": str(pending_root_p),
        "final_root": str(final_root_p),
        "groups": len(groups),
        "pending_lang_written": pending_lang_written,
        "merged_lang_written": merged_lang_written,
        "copied_other_jsons": copied_other,
    }



# ------------------------------------------------------------
# 路徑解析工具 (Path Resolver)
# ------------------------------------------------------------
def resolve_kubejs_root(input_dir: str, *, max_depth: int = 4) -> Path:
    """
    自動解析 KubeJS 的根目錄。
    
    原因：使用者在 UI 介面可能會選取模組包的根目錄（例如 "All the Mods 10"），
    但 KubeJS 資料夾可能藏在更深層。
    
    邏輯：
    1. 往下尋找名為 "kubejs" 的資料夾（不區分大小寫）。
    2. 優先選擇包含 "client_scripts" 子目錄的 kubejs，因為那通常是翻譯發生的核心地帶。
    3. 在同樣條件下，優先選擇層級較淺（靠近根部）的資料夾。
    """
    # 將輸入路徑轉為絕對路徑的 Path 物件
    base = Path(input_dir).resolve()

    # 情況 A：如果目前選取的資料夾本身就是 kubejs
    if base.is_dir() and base.name.lower() == "kubejs":
        return base

    # 情況 B：檢查直接子目錄是否有 kubejs
    direct = base / "kubejs"
    candidates: list[Path] = []
    if direct.is_dir():
        candidates.append(direct)

    # 情況 C：遞迴搜尋（限制深度防止掃描過久）
    base_parts = len(base.parts)
    for p in base.rglob("*"):
        if not p.is_dir():
            continue
            
        # 計算目前路徑相對於起點的深度
        depth = len(p.parts) - base_parts
        if depth > max_depth:
            continue
            
        # 找到名為 kubejs 的目錄就加入候選清單
        if p.name.lower() == "kubejs":
            candidates.append(p)

    # 如果完全找不到任何 kubejs 目錄，則回傳原路徑作為保險
    if not candidates:
        return base

    # 評分機制：用來挑選最正確的 kubejs 資料夾
    def score(p: Path) -> tuple:
        """
        評分邏輯 (分數愈低愈優先):
        1. has_client: 是否有 client_scripts (0 是, 1 否)
        2. depth: 目錄深度 (愈淺愈好)
        """
        has_client = (p / "client_scripts").is_dir()
        depth = len(p.parts) - base_parts
        # Python 的 tuple 比較是逐項進行的
        return (0 if has_client else 1, depth)

    # 根據評分排序，取分數最低者 (index 0)
    candidates.sort(key=score)
    return candidates[0]

# ------------------------------------------------------------
# 第一步：提取與清理 (Extract & Clean)
# ------------------------------------------------------------
def step1_extract_and_clean(
    *,
    pack_or_kubejs_dir: str,  # 模組包或 kubejs 根目錄
    raw_dir: str,             # 存放初步提取結果的原始目錄
    pending_dir: str,         # 存放「待翻譯」檔案的目錄
    final_dir: str,           # 存放「已完成」檔案的目錄
    session=None,             # UI 回話 (Session)，用於回傳進度
    progress_base: float = 0.0, # 目前進度條基數
    progress_span: float = 0.33, # 本步驟佔總進度條的比例
) -> Dict[str, Any]:
    """
    執行 KubeJS 的自動化工作流第一階段：
    1. 解析 KubeJS 目錄位置。
    2. 從 .js 腳本中提取 tooltip/ponder 等文字到 raw 資料夾。
    3. 清理 raw 資料夾，分類出「完成品」與「待翻譯項」。
    """
    
    # 0) 核心路徑解析：自動定位 kubejs 資料夾
    # resolve_kubejs_root 內部已經做了 .resolve()，這裡直接拿結果
    kubejs_dir_path = Path(resolve_kubejs_root(pack_or_kubejs_dir))
    log_info(f"\n🔎 [KubeJS] 確定 KubeJS 目錄為: {kubejs_dir_path}")

    # 1) 提取階段
    log_info(f"📦 [KubeJS] 步驟 1-1：正在提取文字至 -> {raw_dir}")
    from translation_tool.plugins.kubejs.kubejs_tooltip_extract import extract as kjs_extract

    extract_result = kjs_extract(
        source_dir=str(kubejs_dir_path),
        output_dir=str(Path(raw_dir).resolve()),
        session=session,
        progress_base=progress_base,
        progress_span=progress_span * 0.7,
    )
    log_info(f"✅ [KubeJS] 提取完成: 檔案數={extract_result.get('extracted_files')} 總鍵值數={extract_result.get('extracted_keys_total')}")

    # 2) 清理與分流階段
    log_info("🧹 [KubeJS] 步驟 1-2：執行清理並分類 (三方合併邏輯)")

    # 核心修正：定位到 kubejs 的上一層（模組包根目錄）以尋找 assets 語系檔
    modpack_root = str(kubejs_dir_path.parent)

    clean_result = clean_kubejs_from_raw(
        base_dir=modpack_root,
        raw_dir=str(Path(raw_dir).resolve()),
        pending_root=str(Path(pending_dir).resolve()),
        final_root=str(Path(final_dir).resolve()),
    )

    # 更新 UI 進度
    progress(session, min(progress_base + progress_span, 0.999))
    

    # 回傳各階段結果摘要
    return {
        "extract": extract_result,
        "clean": clean_result,
        "kubejs_dir": str(kubejs_dir_path),
        "raw_dir": str(Path(raw_dir).resolve()),
        "pending_dir": str(Path(pending_dir).resolve()),
        "final_dir": str(Path(final_dir).resolve()),
    }




# ------------------------------------------------------------
# Step 2: Translate (pluggable)
# ------------------------------------------------------------
def step2_translate_lm(
    *,
    pending_dir: str,
    output_dir: Optional[str] = None,          # ✅ 新：pipeline 用 output_dir
    translated_dir: Optional[str] = None,      # ✅ 舊：相容你以前的 translated_dir
    session=None,
    progress_base: float = 0.33,
    progress_span: float = 0.33,
    dry_run: bool = False,                     # ✅ 新：讓 dry-run 也能產表
    write_new_cache: bool = True,              # ✅ 新：UI 勾選有效
) -> Dict[str, Any]:
    """
    KubeJS Step2 LM translate wrapper.
    - 支援 output_dir / translated_dir 兩種參數名（避免你其他地方還沒改完就炸）
    - dry_run=True 時：不送 API，但會輸出 preview / 統計（像 FTB）
    """

    # ✅ 統一決定輸出資料夾（優先 output_dir，其次 translated_dir）
    out_arg = output_dir or translated_dir
    if not out_arg:
        raise ValueError("step2_translate_lm: 必須提供 output_dir 或 translated_dir")

    log_info(f"🧠 [KubeJS] Step2 LM translate: {pending_dir} -> {out_arg} (dry_run={dry_run}, write_new_cache={write_new_cache})")

    from translation_tool.plugins.kubejs.kubejs_tooltip_lmtranslator import (
        translate_kubejs_pending_to_zh_tw,
    )

    in_dir = str(Path(pending_dir).resolve())
    out_dir = str(Path(out_arg).resolve())

    # ✅ session wrapper：子流程 0~1 → 外層 base~base+span
    class _ProgressProxy:
        """_ProgressProxy 類別。

        用途：封裝與 _ProgressProxy 相關的狀態與行為。
        維護注意：修改公開方法前請確認外部呼叫點與相容性。
        """
        def __init__(self, parent, base: float, span: float):
            """__init__ 的用途說明。

            Args:
                參數請見函式簽名。
            Returns:
                回傳內容依實作而定；若無顯式回傳則為 None。
            Side Effects:
                可能包含檔案 I/O、網路呼叫或 log 輸出等副作用（依實作而定）。
            """
            self.parent = parent
            self.base = float(base)
            self.span = float(span)

        def set_progress(self, p: float):
            """set_progress 的用途說明。

            Args:
                參數請見函式簽名。
            Returns:
                回傳內容依實作而定；若無顯式回傳則為 None。
            Side Effects:
                可能包含檔案 I/O、網路呼叫或 log 輸出等副作用（依實作而定）。
            """
            if not self.parent or not hasattr(self.parent, "set_progress"):
                return
            try:
                p = 0.0 if p is None else float(p)
                if p < 0:
                    p = 0.0
                elif p > 1:
                    p = 1.0
                self.parent.set_progress(self.base + p * self.span)
            except Exception:
                pass

        # 可選轉發
        def set_status(self, msg: str):
            """set_status 的用途說明。

            Args:
                參數請見函式簽名。
            Returns:
                回傳內容依實作而定；若無顯式回傳則為 None。
            Side Effects:
                可能包含檔案 I/O、網路呼叫或 log 輸出等副作用（依實作而定）。
            """
            if self.parent and hasattr(self.parent, "set_status"):
                try:
                    self.parent.set_status(msg)
                except Exception:
                    pass

    proxy = _ProgressProxy(session, progress_base, progress_span)

    # ✅ 呼叫底層（dry_run / write_new_cache 由外部控制）
    result = translate_kubejs_pending_to_zh_tw(
        pending_dir=in_dir,
        output_dir=out_dir,
        session=proxy,
        dry_run=bool(dry_run),
        write_new_cache=bool(write_new_cache),
    )

    # ✅ Step2 結束推到區間尾端
    if session and hasattr(session, "set_progress"):
        try:
            session.set_progress(progress_base + progress_span)
        except Exception:
            pass

    return result



# ------------------------------------------------------------
# 第三步：注入 (Inject)
# ------------------------------------------------------------
def step3_inject(
    *,
    pack_or_kubejs_dir: str,  # 模組包或 kubejs 根目錄
    src_dir: str,             # 翻譯來源目錄（通常是經過 AI 或人工翻譯後的 en_us 檔案）
    final_dir: str,           # 最終產出目錄（存放注入後的結果）
    session=None,             # UI 回話 (Session)，用於回傳進度
    progress_base: float = 0.66, # 進度條起點（假設前兩步已完成 66%）
    progress_span: float = 0.33, # 本步驟佔總進度條的比例（約佔最後三分之一）
) -> Dict[str, Any]:
    """
    執行 KubeJS 的自動化工作流第三階段：
    將處理好的翻譯內容「注入」回 KubeJS 系統中。
    
    此步驟通常會：
    1. 讀取 src_dir 中的翻譯。
    2. 對比 kubejs_dir 裡的原始腳本或語言檔。
    3. 將最終結果輸出至 final_dir。
    """
    
    # 0) 再次確認 KubeJS 根目錄路徑，確保操作對象正確
    kubejs_dir = resolve_kubejs_root(pack_or_kubejs_dir)
    log_info(f"⚡[KubeJS] 步驟 3：開始注入翻譯 -> 目標目錄: {final_dir}")

    # 1) 載入注入插件
    # 這裡的 kjs_inject 是實際執行「腳本修改」或「語言檔覆蓋」的核心邏輯
    from translation_tool.plugins.kubejs.kubejs_tooltip_inject import inject as kjs_inject

    # 2) 執行注入操作
    # 傳入三個路徑：原始位置、翻譯源、輸出位置
    # 同時傳遞 session 與進度參數，讓底層函式能即時更新 UI 狀態
    return kjs_inject(
        str(kubejs_dir),
        str(Path(src_dir).resolve()),
        str(Path(final_dir).resolve()),
        session=session,
        progress_base=progress_base,
        progress_span=progress_span,
    )



# ------------------------------------------------------------
# 流程進入點 (Pipeline Entry)
# ------------------------------------------------------------
def run_kubejs_pipeline(
    *,
    input_dir: str,                # 輸入路徑 (模組包根目錄或 kubejs)
    output_dir: Optional[str],     # 輸出根目錄
    session=None,                  # UI 溝通物件，用於更新進度條
    dry_run: bool = False,         # 測試模式：若為 True，則不執行耗時或具破壞性的操作
    step_extract: bool = True,     # 開關：步驟 1 (提取與清理)
    step_translate: bool = True,   # 開關：步驟 2 (大型語言模型 AI 翻譯)
    step_inject: bool = True,      # 開關：步驟 3 (注入回腳本)
    translator_fn: Optional[Callable[..., Dict[str, Any]]] = None, # 外部翻譯函式
    write_new_cache: bool = False,  # 是否寫入新的快取 (僅在 step2_translate_lm 有效)
) -> Dict[str, Any]:
    """
    KubeJS 翻譯全流程控制中心。
    管理 raw -> pending -> translated -> final 的資料流。
    """
    base = Path(input_dir).resolve()
    # 定義輸出根路徑，預設在輸入目錄下的 Output 資料夾
    out_root = Path(output_dir).resolve() if output_dir else (base / "Output")

    # 定義四個階段的專屬資料夾（多一層 kubejs，避免與其他輸出混在一起）
    raw_dir = out_root / "kubejs" / "raw" / "kubejs"          # 剛提取出來的原始檔
    pending_dir = out_root / "kubejs" / "待翻譯" / "kubejs"    # 整理後，真正需要翻的 en_us
    translated_dir = out_root / "kubejs" / "LM翻譯後" / "kubejs" # AI 翻好的檔案
    final_dir = out_root / "kubejs" / "完成" / "kubejs"        # 注入完成的最終成果

    # 初始化目錄環境
    for d in [raw_dir, pending_dir, translated_dir, final_dir]:
        d.mkdir(parents=True, exist_ok=True)

    log_info("🧩 [KubeJS] 流程開始啟動")
    if dry_run:
        log_info("🧪 [KubeJS] 注意：目前為 DRY-RUN 測試模式，不會執行實際動作")

    result: Dict[str, Any] = {
        "paths": {
            "input": str(base),
            "raw": str(raw_dir),
            "pending": str(pending_dir),
            "translated": str(translated_dir),
            "final": str(final_dir),
        }
    }

    # ------------------------------------------------------------
    # 步驟 1: 提取與清理 (Extract + Clean)
    # 進度範圍: 0.0 -> 0.33
    # ------------------------------------------------------------
    start_time = time.perf_counter()
    if step_extract:
        result["step1"] = step1_extract_and_clean(
            pack_or_kubejs_dir=str(base),
            raw_dir=str(raw_dir),
            pending_dir=str(pending_dir),
            final_dir=str(final_dir),
            session=session,
            progress_base=0.0,
            progress_span=0.33,
        )
    else:
        log_info("⏭️ [KubeJS] 跳過步驟 1")
        progress(session, 0.33)

    # 統計「待翻譯」資料夾中有多少個 Key 需要處理
    def _count_pending_lang_keys(pending_dir: Path) -> int:
        """_count_pending_lang_keys 的用途說明。

        Args:
            參數請見函式簽名。
        Returns:
            回傳內容依實作而定；若無顯式回傳則為 None。
        Side Effects:
            可能包含檔案 I/O、網路呼叫或 log 輸出等副作用（依實作而定）。
        """
        total = 0
        for p in pending_dir.rglob("*.json"):
            try:
                data = orjson.loads(p.read_bytes())
                if isinstance(data, dict):
                    total += len(data)
            except Exception:
                pass
        return total

    def _log_kubejs_step2_stats(step2_res: Dict[str, Any]) -> None:
        """_log_kubejs_step2_stats 的用途說明。

        Args:
            參數請見函式簽名。
        Returns:
            回傳內容依實作而定；若無顯式回傳則為 None。
        Side Effects:
            可能包含檔案 I/O、網路呼叫或 log 輸出等副作用（依實作而定）。
        """
        if not isinstance(step2_res, dict):
            return

        if step2_res.get("skipped"):
            log_info(
                "[KubeJS] Step2 霈?嚗??? %s",
                step2_res.get("reason"),
            )
            return

        is_dry = bool(step2_res.get("dry_run"))
        files = step2_res.get("files", step2_res.get("written_files"))
        total_keys = step2_res.get("total_keys")
        cache_hit = step2_res.get("cache_hit")
        cache_miss = step2_res.get("cache_miss")
        api_translated = step2_res.get("api_translated")
        preview_path = step2_res.get("preview_path")
        records_json = step2_res.get("records_json")
        records_csv = step2_res.get("records_csv")
        batch_size = _get_default_batch_size("kubejs", None)
        avg_batch_sec = step2_res.get("avg_batch_sec")
        est_sec_per_batch = avg_batch_sec
        est_batches = (
            math.ceil(cache_miss / batch_size)
            if isinstance(cache_miss, int) and batch_size > 0
            else None
        )

        log_info(
            "[KubeJS] Step2 統計 %s | files=%s | total_keys=%s | cache_hit=%s | cache_miss=%s",
            "DRY-RUN" if is_dry else "翻譯",
            files,
            total_keys,
            cache_hit,
            cache_miss,
        )

        if is_dry:
            if preview_path:
                log_info("[KubeJS] DRY-RUN preview: %s", preview_path)
        else:
            if api_translated is not None:
                log_info("[KubeJS] API 翻譯: %s", api_translated)
            if records_json or records_csv:
                log_info("[KubeJS] records: json=%s | csv=%s", records_json, records_csv)
        if est_batches is not None:
            log_info(
                "[KubeJS] 預估批次：%s (batch_size=%s)",
                est_batches,
                batch_size,
            )
        if avg_batch_sec:
            log_info("[KubeJS] 平均每批耗時(本次)：%.2fs", avg_batch_sec)
        if est_batches is not None and est_sec_per_batch:
            total_sec = int(est_batches * est_sec_per_batch)
            m, s = divmod(total_sec, 60)
            h, m = divmod(m, 60)
            eta_txt = f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"
            log_info("[KubeJS] 預估總耗時：%s", eta_txt)

        per_file = step2_res.get("per_file")
        if isinstance(per_file, list) and per_file:
            log_info("[KubeJS] 檔案批次估算：")
            for row in per_file:
                if not isinstance(row, dict):
                    continue
                f = row.get("file")
                miss = row.get("cache_miss")
                dst = row.get("dst")
                f_batches = (
                    math.ceil(miss / batch_size)
                    if isinstance(miss, int) and batch_size > 0
                    else None
                )
                if f_batches is None:
                    continue
                log_info(
                    "[KubeJS] - %s | cache_miss=%s | batches=%s | dst=%s",
                    f,
                    miss,
                    f_batches,
                    dst,
                )
    

    pending_lang_keys = _count_pending_lang_keys(pending_dir)
    log_info(f"🧾 [KubeJS] 統計：共有 {pending_lang_keys} 個 Key 待翻譯")

    # 智慧跳過：如果已經全部翻譯完成，就自動跳過 Step 2
    if pending_lang_keys == 0:
        log_info("✅ [KubeJS] 無待翻譯項目，自動跳過步驟 2 (AI 翻譯)")
        result["step2"] = {"skipped": True, "reason": "pending lang keys = 0"}
        progress(session, 0.66)
    
    # ------------------------------------------------------------
    # 步驟 2: AI 翻譯 (LM Translate)
    # 進度範圍: 0.33 -> 0.66
    # ------------------------------------------------------------
    elif step_translate:  # 只有在 pending_lang_keys > 0 時才會進入
        # ✅ 不再因為 translator_fn None 就跳過：預設用 step2_translate_lm
        if translator_fn is None:
            translator_fn = step2_translate_lm

        if dry_run:
            # ✅ 乾跑：不送 API，但要跑 Step2 的分析/快取分流/輸出報表
            log_info("🧪 [KubeJS] 測試模式：執行 Step2 分析/報表（不送 API）")
            result["step2"] = translator_fn(
                pending_dir=str(pending_dir),
                output_dir=str(translated_dir),
                session=session,
                progress_base=0.33,
                progress_span=0.33,
                dry_run=True,            # ✅ 關鍵：讓 step2_translate_lm 走 dry-run preview
                write_new_cache=False,   # ✅ dry-run 通常不寫新快取（你也可以改成 True）
            )
            _log_kubejs_step2_stats(result["step2"])
            progress(session, 0.66)

        else:
            log_info("🧠 [KubeJS] 開始 AI 翻譯流程...")
            result["step2"] = translator_fn(
                pending_dir=str(pending_dir),
                output_dir=str(translated_dir),
                session=session,
                progress_base=0.33,
                progress_span=0.33,
                dry_run=False,
                write_new_cache=write_new_cache,
            )
            _log_kubejs_step2_stats(result["step2"])
            progress(session, 0.66)

    else:
        log_info("⏭️ [KubeJS] 跳過步驟 2")
        progress(session, 0.66)


        

    # ------------------------------------------------------------
    # 步驟 3: 注入 (Inject)
    # 進度範圍: 0.66 -> 0.99
    # ------------------------------------------------------------
    if step_inject:
        if dry_run:
            log_info("🧪 [KubeJS] 測試模式：跳過注入操作")
            result["step3"] = {"skipped": True, "reason": "dry_run"}
        else:
            # 容錯逻辑：如果翻譯資料夾有東西就用翻譯後的，否則拿 pending 的（原封不動注入）
            src_for_inject = translated_dir if translated_dir.exists() and any(translated_dir.rglob("*.json")) else pending_dir
            log_info(f"💉 [KubeJS] 執行注入：來源為 {src_for_inject.name}")
            result["step3"] = step3_inject(
                pack_or_kubejs_dir=str(base),
                src_dir=str(src_for_inject),
                final_dir=str(final_dir),
                session=session,
                progress_base=0.66,
                progress_span=0.33,
            )
    else:
        log_info("⏭️ [KubeJS] 跳過步驟 3")

    duration = get_formatted_duration(start_time)
    # 結尾明細（對齊 FTB 風格，避免前面訊息被沖掉）
    step2_summary = result.get("step2", {}) if isinstance(result.get("step2"), dict) else {}
    if step2_summary:
        log_info("✅ [KubeJS] Step2 統計明細：")
        summary = dict(step2_summary)
        summary.pop("per_file", None)
        log_info("%s", orjson.dumps(summary, option=orjson.OPT_INDENT_2).decode("utf-8"))

        # 白話總結（避免太多細節被洗掉）
        if not summary.get("skipped"):
            total_keys = summary.get("total_keys")
            cache_hit = summary.get("cache_hit")
            cache_miss = summary.get("cache_miss")
            files = summary.get("files", summary.get("written_files"))
            batch_size = _get_default_batch_size("kubejs", None)
            est_batches = (
                math.ceil(cache_miss / batch_size)
                if isinstance(cache_miss, int) and batch_size > 0
                else None
            )
            log_info(
                "\n🧾 [KubeJS] 摘要：📁 共 %s 個檔案、🔢 總計 %s 個 Key；✅ 快取命中 %s；🤖 需要 AI 翻譯 %s 條；🧮 預估批次 %s 次。",
                files,
                total_keys,
                cache_hit,
                cache_miss,
                est_batches,
            )

    log_info(f"🎉 [KubeJS] 任務完成！ {duration}")


    progress(session, 0.999)
    return result


