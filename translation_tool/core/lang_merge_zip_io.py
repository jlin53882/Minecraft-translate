"""translation_tool/core/lang_merge_zip_io.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import logging
import os
import zipfile
from typing import Any, Dict

import orjson as json

from ..utils.config_manager import load_config

logger = logging.getLogger(__name__)

def _read_text_from_zip(zf: zipfile.ZipFile, path: str) -> str:
    """
    從 ZipFile 物件中讀取指定路徑的檔案內容，並解碼為字串。
    Args:
        zf (zipfile.ZipFile): 已開啟的 ZipFile 物件。
        path (str): Zip 檔案內部的路徑。
    Returns:
        str: 解碼後的文字內容。
    """
    
    # 1. 以位元組形式讀取檔案的原始內容
    with zf.open(path) as f:
        raw = f.read()
    # 2. 嘗試使用 UTF-8 進行標準解碼
    # 優先使用 utf-8-sig，它會自動過濾掉 UTF-8 的 BOM (\ufeff)
    try:
        return raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        # 如果 utf-8 失敗，嘗試繁體/簡體常用的 GBK (常見於舊模組)
        try:
            return raw.decode('gbk')
        except UnicodeDecodeError:
            # 最後才用 ignore 模式保命
            return raw.decode('utf-8', errors='replace')

def _read_json_from_zip(zf: zipfile.ZipFile, path: str) -> Dict[str, Any]:
    """
    從 ZipFile 中讀取指定路徑的檔案，並嘗試將其解析為 JSON 物件 (字典)。
    自動處理 UTF-8 BOM。
    採用事前清理機制，移除 BOM 與首尾空白，確保解析成功。

    Args:
        zf (zipfile.ZipFile): 已開啟的 ZipFile 物件。
        path (str): Zip 檔案內部的 JSON 檔案路徑。

    Returns:
        Dict[str, Any]: 解析後的 JSON 資料 (Python 字典)，失敗則返回空字典。
    """
    # 1. 取得原始文字
    text = _read_text_from_zip(zf, path)
    if not text:
            return {}
    
    # 2. 事前處理：徹底移除 BOM 與所有不可見字元 (空格, \n, \r, \t)
    # .strip() 移除首尾空白，.lstrip('\ufeff') 移除 UTF-8 BOM
    cleaned_text = text.strip().lstrip('\ufeff')

    # 3. 如果清理後內容為空，直接回傳
    if not cleaned_text:
        return {}

    try:
        # 使用 orjson (你 alias 為 json) 解析
        return json.loads(cleaned_text)
    except Exception as e:
        # 如果還是失敗，嘗試將錯誤資訊記錄下來，方便排查
        logger.warning(f"JSON 解析依然失敗 (路徑: {path}): {e}")
        # 在某些極端情況下，檔案可能是編碼損毀，回傳空字典避免程式崩潰
        return {}

def _write_bytes_atomic(path: str, data: bytes) -> None:
    """
    將位元組資料以「原子性」的方式寫入檔案，確保資料寫入的穩定性。
    使用「寫入臨時檔案，然後原子性替換」的模式。

    Args:
        path (str): 最終目標檔案路徑。
        data (bytes): 要寫入的位元組資料。
    """
    # 1. 確保目標檔案路徑的資料夾存在
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # 2. 定義臨時檔案名稱 (用於原子性寫入)
    tmp = path + ".tmp"
    # 3. 將資料寫入臨時檔案
    with open(tmp, "wb") as f:
        f.write(data)  
    # 4. 原子性替換：將完整的臨時檔案替換為目標檔案。
    #    這個操作在大多數 OS 上是原子的，防止在寫入過程中斷電或崩潰導致檔案損壞。
    os.replace(tmp, path)

def _write_text_atomic(path: str, text: str) -> None:
    """
    將文字資料 (UTF-8 編碼) 以「原子性」的方式寫入檔案。
    實作方式與 _write_bytes_atomic 類似。

    Args:
        path (str): 最終目標檔案路徑。
        text (str): 要寫入的文字內容。
    """
    # 1. 確保目標檔案路徑的資料夾存在
    os.makedirs(os.path.dirname(path), exist_ok=True)    
    # 2. 定義臨時檔案名稱
    tmp = path + ".tmp"   
    # 3. 將文字資料寫入臨時檔案，指定 UTF-8 編碼
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    # 4. 原子性替換
    os.replace(tmp, path)

def quarantine_copy_from_zip(
    zf: zipfile.ZipFile,
    zip_path: str,
    output_dir: str,
    reason: str,
    extra_text=None
):
    """
    將解析失敗的檔案原樣複製到：
    output_dir/skipped_json/<zip 原始路徑>

    目錄結構會與「待翻譯」完全一致，方便人工比對與修復。
    """

    # skipped_json/ + 原始 zip 路徑（例如 assets/xxx）
    quarantine_root_name= load_config().get("lang_merger", {}).get("quarantine_folder_name", "skipped_json")
    quarantine_root = os.path.join(output_dir, quarantine_root_name)
    target_path = os.path.join(quarantine_root, zip_path)

    os.makedirs(os.path.dirname(target_path), exist_ok=True)

    try:
        # 原樣複製 bytes（不 decode、不解析）
        raw_bytes = zf.read(zip_path)
        with open(target_path, "wb") as f:
            f.write(raw_bytes)

        # 附加原因說明檔（同層）
        reason_path = target_path + ".reason.txt"
        with open(reason_path, "w", encoding="utf-8") as f:
            f.write(reason)
        
        # ⭐ 新增：如果提供額外文本（如詳細報錯），則寫入 .detail.txt
        if extra_text:
            detail_path = target_path + ".detail.txt"
            with open(detail_path, "w", encoding="utf-8") as f:
                f.write(extra_text)

        logger.warning(
            f"[隔離] 檔案已複製至 {target_path}（原因: {reason}）"
        )

    except Exception as e:
        logger.error(
            f"[隔離失敗] 無法複製檔案 {zip_path}: {e}",
            exc_info=True
        )
