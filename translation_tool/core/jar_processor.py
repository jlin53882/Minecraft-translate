"""translation_tool/core/jar_processor.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

# /minecraft_translator_flet/translation_tool/core/jar_processor.py (路徑分隔符修正版)

import os
import zipfile
import re
import logging
import shutil
import hashlib
import concurrent.futures
from typing import List, Generator, Dict, Any

from ..utils.config_manager import load_config

log = logging.getLogger(__name__)

# --- 通用輔助函式 (保持不變) ---
def _get_file_hash(data: bytes) -> str:
    """_get_file_hash 的用途說明。

    Args:
        參數請見函式簽名。
    Returns:
        回傳內容依實作而定；若無顯式回傳則為 None。
    Side Effects:
        可能包含檔案 I/O、網路呼叫或 log 輸出等副作用（依實作而定）。
    """
    return hashlib.sha256(data).hexdigest()

def find_jar_files(folder_path: str) -> List[str]:
    """find_jar_files 的用途說明。

    Args:
        參數請見函式簽名。
    Returns:
        回傳內容依實作而定；若無顯式回傳則為 None。
    Side Effects:
        可能包含檔案 I/O、網路呼叫或 log 輸出等副作用（依實作而定）。
    """
    jar_files = []
    for root, _, files in os.walk(folder_path):
        for file in files:
            if file.endswith('.jar'):
                jar_files.append(os.path.join(root, file))
    log.info(f"在 '{folder_path}' 中找到 {len(jar_files)} 個 .jar 檔案。")
    return jar_files

# 定義一個正則表達式來匹配並移除常見的版本號和平台標籤
# 範例：-1.21.1-1.1.0, -neoforge-7.0.22, -4.6.1.524, -1.20.6-1.21.4-1.9.9
VERSION_REGEX = re.compile(
    r'[-_](?:[a-zA-Z]+-)?\d+(?:\.\d+)+(?:[-_.][a-zA-Z0-9]+)*$',  # 匹配版本號，如 -1.21.1-1.1.0 或 -7.0.22
    re.IGNORECASE
)

def _extract_from_jar(jar_path: str, output_root: str, target_regex: re.Pattern) -> Dict[str, Any]:
    """
    從 JAR 檔案中提取目標檔案，並根據路徑結構確定輸出位置：
    1. 標準路徑 (assets/...): 輸出到 output_root/assets/...
    2. 非標準/根級路徑 (lang/, data/, ae2ct/): 輸出到 output_root/<jar_name>_extracted/...
    同時，會移除輸出資料夾名稱中的版本號。
    """
    extracted_count = 0
    skipped_count = 0
    
    jar_filename = os.path.basename(jar_path)
    # 原始的 JAR 檔案名稱 (不含 .jar)
    jar_filename_base_full = os.path.splitext(jar_filename)[0]

    # --- 步驟 1: 清理 JAR 檔案名稱以獲得簡潔的 mod_name ---
    jar_filename_base = jar_filename_base_full
    
    # 1.1 移除常見的平台標籤和構建資訊
    clean_name = re.sub(r'[-_](neoforge|forge|fabric|quilt|build|release|alpha|beta)[-_]?', r'-', jar_filename_base, flags=re.IGNORECASE).strip('-_')
    
    # 1.2 移除剩餘的版本號
    match_version = VERSION_REGEX.search(clean_name)
    if match_version:
        # 移除版本號部分，並清理末尾可能多餘的橫線或底線
        jar_filename_base = clean_name[:match_version.start()].strip('-_')
    else:
        # 如果沒有匹配到版本號，則使用清理過的名稱
        jar_filename_base = clean_name
        
    # 處理特殊情況：如果清理後名稱為空，則使用原完整名稱
    if not jar_filename_base:
        jar_filename_base = jar_filename_base_full
    
    # --------------------------------------------------------

    try:
        with zipfile.ZipFile(jar_path, 'r') as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue
                
                # 為了相容性，將所有反斜線替換為正斜線後再進行匹配
                normalized_path = member.filename.replace('\\', '/')
                match = target_regex.search(normalized_path)
                
                if match:
                    # --- 步驟 2: 區分輸出路徑 ---
                    
                    # 判斷是否為標準 assets/ 開頭
                    is_standard_assets = normalized_path.startswith('assets/')
                    
                    if is_standard_assets:
                        # 情況 A: 標準資源 (e.g., assets/ae2/lang/en_us.json)
                        # 輸出路徑直接是 output_root / assets/... (路徑不變)
                        final_output_path = os.path.join(output_root, normalized_path)
                    else:
                        # 情況 B: 非標準/根級資源 (e.g., lang/en_us.json, ae2ct/lang/en_us.json)
                        # 輸出路徑是 output_root / <jar_name>_extracted / ...
                        # 這裡使用清理過後的 jar_filename_base
                        final_mod_folder = f"{jar_filename_base}_extracted"
                        final_output_path = os.path.join(output_root, final_mod_folder, normalized_path)
                    
                    # --- 步驟 3: 檔案處理 (與您的原始邏輯相同) ---

                    with zf.open(member) as source:
                        source_data = source.read()
                        source_hash = _get_file_hash(source_data)

                    if os.path.exists(final_output_path):
                        with open(final_output_path, 'rb') as existing_file:
                            existing_hash = _get_file_hash(existing_file.read())
                        if source_hash == existing_hash:
                            skipped_count += 1
                            continue

                    os.makedirs(os.path.dirname(final_output_path), exist_ok=True)
                    with open(final_output_path, 'wb') as target:
                        target.write(source_data)
                    extracted_count += 1
                    
        return {"status": "success", "extracted": extracted_count, "skipped": skipped_count}
    except Exception as e:
        log.error(f"處理 {os.path.basename(jar_path)} 時發生錯誤: {e}")
        return {"status": "error", "extracted": 0, "skipped": 0}

def _run_extraction_process(mods_dir: str, output_dir: str, target_regex: re.Pattern, process_name: str) -> Generator[Dict[str, Any], None, None]:
    # (此函式邏輯不變)
    """_run_extraction_process 的用途說明。

    Args:
        參數請見函式簽名。
    Returns:
        回傳內容依實作而定；若無顯式回傳則為 None。
    Side Effects:
        可能包含檔案 I/O、網路呼叫或 log 輸出等副作用（依實作而定）。
    """
    os.makedirs(output_dir, exist_ok=True)
    jar_files = find_jar_files(mods_dir)
    total_jars = len(jar_files)

    if total_jars == 0:
        log.info( f"在 '{mods_dir}' 中未找到任何 .jar 檔案。")
        yield {"progress": 1.0, }#"log": f"在 '{mods_dir}' 中未找到任何 .jar 檔案。"}
        return

    log.info(f"開始從 {total_jars} 個 .jar 檔案中提取 {process_name} 檔案...")
    yield {"progress": 0.0, }#"log": f"開始從 {total_jars} 個 .jar 檔案中提取 {process_name} 檔案..."}

    processed_count = 0
    total_extracted = 0
    total_skipped = 0
    max_workers = load_config().get("translation", {}).get("parallel_execution_workers") or os.cpu_count()
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_jar = {executor.submit(_extract_from_jar, jar, output_dir, target_regex): jar for jar in jar_files}
        
        for future in concurrent.futures.as_completed(future_to_jar):
            jar_path = future_to_jar[future]
            processed_count += 1
            progress = processed_count / total_jars
            
            try:
                result = future.result()
                status_msg = ""
                if result["status"] == "success":
                    total_extracted += result["extracted"]
                    total_skipped += result["skipped"]
                    if result["extracted"] > 0:
                        status_msg = f"成功提取 {result['extracted']} 個新檔案"
                    elif result["skipped"] > 0:
                        status_msg = f"檔案已存在且相同，跳過"
                    else:
                        status_msg = f"未找到目標檔案"
                else:
                    status_msg = "處理時發生錯誤"

                log_msg = f"[{processed_count}/{total_jars}] {os.path.basename(jar_path)}: {status_msg}"
                log.info(log_msg)
                yield {"progress": progress, }#"log": log_msg}

            except Exception as exc:
                log.error(f"提取 {os.path.basename(jar_path)} 時產生例外: {exc}")
                yield {"progress": progress, }#"log": f"處理 {os.path.basename(jar_path)} 失敗: {exc}", "error": True}
    
    summary = f"--- {process_name} 提取完成！ ---\n" \
              f"已檢查 {processed_count}/{total_jars} 個 JAR 檔案。\n" \
              f"  - 新提取或更新的檔案: {total_extracted} 個\n" \
              f"  - 因內容相同而跳過的檔案: {total_skipped} 個"
    log.info(summary)
    yield {"progress": 1.0, }#"log": summary}

# --- 供 services.py 呼叫的公開函式 ---

# /minecraft_translator_flet/translation_tool/core/jar_processor.py

def extract_lang_files_generator(
    mods_dir: str,
    output_dir: str
) -> Generator[Dict[str, Any], None, None]:
    """
    從 mods 資料夾中的所有 .jar 提取：
    1. Lang 檔案（en_us / zh_cn / zh_tw）
    2. Item / Block icon（assets/*/textures/item|block/*.png）

    外部呼叫方式保持不變：
        yield from extract_lang_files_generator(mods_dir, output_dir)
    """

    # ==================================================
    # 1️⃣ Lang 檔案（最終穩定版 regex）
    # ==================================================
    # 支援：
    # - assets/<modid>/lang/en_us.json
    # - lang/en_us.json
    # - <anything>/lang/en_us.json
    #
    # 實際輸出路徑由 _extract_from_jar 負責判斷：
    # - assets/... → output_root/assets/...
    # - 非標準 → output_root/<jar>_extracted/...
    #
    # Regex 說明：
    # \.(json|lang)$  -> 同時匹配兩種副檔名
    # (en_us|zh_cn|zh_tw) -> 鎖定目標語言
    
    lang_file_regex = re.compile(
        r'(?:assets/([^/]+)/)?lang/(en_us|zh_cn|zh_tw)\.(json|lang)$',
        re.IGNORECASE
    )

    yield from _run_extraction_process(
        mods_dir=mods_dir,
        output_dir=output_dir,
        target_regex=lang_file_regex,
        process_name="Lang"
    )

    # ==================================================
    # 2️⃣ Item / Block Icons（方案一） 暫時先不要用
    # ==================================================
    # 支援：
    # - assets/<modid>/textures/item/*.png
    # - assets/<modid>/textures/block/*.png
    # - 含子資料夾（tools/, materials/ ...）
    #
#    icon_file_regex = re.compile(
#        r'^assets/[^/]+/textures/(?:item|block)/.+\.png$',
#        re.IGNORECASE
#    )
#
#    yield {"progress": None, }#"log": "開始提取 Item / Block Icons ..."}
#
#    yield from _run_extraction_process(
#        mods_dir=mods_dir,
#        output_dir=output_dir,
#        target_regex=icon_file_regex,
#        process_name="Item/Block Icons"
#    )


# 定義您想提取的語言代碼列表
LANG_CODES = ['en_us', 'zh_tw', 'zh_cn']
# 將語言代碼組合成一個供 Regex 匹配的群組，例如: (en_us|zh_tw|zh_cn)
#lang_pattern = '|'.join(re.escape(lang) for lang in LANG_CODES)
lang_pattern = r'_?(?:' + '|'.join(map(re.escape, LANG_CODES)) + r')'

# 匹配路徑：
# 1. assets/
# 2. [任何mod名稱]/
# 3. patchouli_books/
# 4. [任何mod名稱]/
# 5. (en_us|zh_tw|zh_cn)/  <-- 這是關鍵的語言資料夾
# 6. .* <-- 之後的任何內容
BOOK_PATH_REGEX_DUAL_STRUCTURE = re.compile(
    rf'(assets|data)/([^/]+)/'             # 第一部分：標準資源路徑結構
    # ----------------------------------------------------
    # 匹配 'assets/' 或 'data/'，並捕獲資源根資料夾 (Group 1)
    # 匹配 Mod ID (Group 2)
    # ----------------------------------------------------
    
    rf'(patchouli_books|book|manual|guidebook)/'     # 第二部分：書籍類型資料夾
    # ----------------------------------------------------
    # 匹配特定的書籍根資料夾名稱 (Group 3)
    # ----------------------------------------------------
    
    rf'(?:([^/]+)/)?'                      # 第三部分：可選的 Book ID
    # ----------------------------------------------------
    # 匹配並捕獲 Book ID（例如 'guide'），這是可選的 (Group 4)
    # (?:...) 是非捕獲組，確保 `?` 作用於整個 Book ID 部分。
    # ----------------------------------------------------
    
    # 第四部分：處理語言層或 book.json 結尾 (使用 Alternation |)
    rf'(?:'
        
        # --- 情況 A: 帶語言代碼層 ---
        # 4. 語言代碼層（`lang_pattern` 應為預先定義的語言代碼列表，如 (en_us|zh_tw)）
        rf'({lang_pattern})(/.*)?'         # A: 捕獲語言代碼 (Group 5), 捕獲之後的路徑 (Group 6)
        # ----------------------------------------------------
        # 匹配並捕獲語言代碼（如 'en_us' 或 'zh_tw'） (Group 5)
        # 匹配並捕獲語言代碼之後的所有路徑，這是可選的 (Group 6)
        # ----------------------------------------------------
        
        rf'|'
        
        # --- 情況 B: 直接 book.json 結尾（不帶語言代碼層）---
        rf'book\.json'                     # B: 直接匹配 book.json 檔案名稱
        # ----------------------------------------------------
        # 匹配精確的檔案名稱 'book.json'。
        # ----------------------------------------------------
        
    rf')$',
    re.IGNORECASE
)

def extract_book_files_generator(mods_dir: str, output_dir: str) -> Generator[Dict[str, Any], None, None]:
    """從 mods 資料夾中的所有 .jar 提取 Patchouli book 檔案。"""
    # 這裡的 Patchouli regex 由於結構複雜，需要調整 _extract_from_jar 中的相對路徑邏輯來處理。
    # 為了保持簡單和解決 lang 問題，我們暫時不修改 book_path_regex 的調用，
    # 而是依賴新的 _extract_from_jar 中的通用相對路徑處理邏輯。 
    # 新增 book 資料夾撈取邏輯：
    #book_path_regex = re.compile(r'(assets|data)/([^/]+)/(patchouli_books|book)(/.*)?$', re.IGNORECASE) # 原始版本
    book_path_regex = BOOK_PATH_REGEX_DUAL_STRUCTURE # 更新後的版本，包含語言資料夾匹配
    yield from _run_extraction_process(mods_dir, output_dir, book_path_regex, "Patchouli Book")


# --- 新增功能：預覽模式 ---

def preview_extraction_generator(mods_dir: str, mode: str) -> Generator[Dict[str, Any], None, None]:
    """
    預覽將要提取的檔案（不實際提取，generator 版本可即時顯示進度）
    
    Args:
        mods_dir: Mods 資料夾路徑
        mode: 'lang' 或 'book'
    
    Yields:
        {
            'progress': float (0.0-1.0),
            'current': int (目前處理到第幾個),
            'total': int (總共幾個 JAR),
            'result': dict (最後一次 yield 包含完整結果)
        }
    """
    jar_files = find_jar_files(mods_dir)
    total_jars = len(jar_files)
    
    if total_jars == 0:
        yield {
            'progress': 1.0,
            'result': {
                'total_jars': 0,
                'preview_results': [],
                'total_files': 0,
                'total_size_mb': 0
            }
        }
        return
    
    if mode == 'lang':
        target_regex = re.compile(
            r'(?:assets/([^/]+)/)?lang/(en_us|zh_cn|zh_tw)\.(json|lang)$',
            re.IGNORECASE
        )
    elif mode == 'book':
        target_regex = BOOK_PATH_REGEX_DUAL_STRUCTURE
    else:
        yield {'error': f'未知模式: {mode}'}
        return
    
    preview_results = []
    total_files = 0
    total_size_bytes = 0
    
    for idx, jar_path in enumerate(jar_files, 1):
        jar_name = os.path.basename(jar_path)
        jar_size = os.path.getsize(jar_path)
        matched_files = []
        
        try:
            with zipfile.ZipFile(jar_path, 'r') as zf:
                for member in zf.infolist():
                    if member.is_dir():
                        continue
                    
                    normalized_path = member.filename.replace('\\', '/')
                    if target_regex.search(normalized_path):
                        matched_files.append(normalized_path)
                        total_size_bytes += member.file_size
            
            if matched_files:  # 只記錄有找到檔案的 JAR
                preview_results.append({
                    'jar': jar_name,
                    'files': matched_files,
                    'count': len(matched_files),
                    'size_mb': jar_size / (1024 ** 2)
                })
                total_files += len(matched_files)
        
        except Exception as e:
            log.warning(f"預覽 {jar_name} 時發生錯誤: {e}")
        
        # Yield 進度
        progress = idx / total_jars
        yield {
            'progress': progress,
            'current': idx,
            'total': total_jars,
        }
    
    # 最後 yield 完整結果
    yield {
        'progress': 1.0,
        'current': total_jars,
        'total': total_jars,
        'result': {
            'total_jars': total_jars,
            'preview_results': preview_results,
            'total_files': total_files,
            'total_size_mb': total_size_bytes / (1024 ** 2)
        }
    }


# --- 新增功能：提取結果摘要 ---

class ExtractionSummary:
    """提取結果摘要（記錄成功/警告/失敗）"""
    
    def __init__(self):
        """__init__ 的用途說明。

        Args:
            參數請見函式簽名。
        Returns:
            回傳內容依實作而定；若無顯式回傳則為 None。
        Side Effects:
            可能包含檔案 I/O、網路呼叫或 log 輸出等副作用（依實作而定）。
        """
        self.success = []
        self.warnings = []
        self.failures = []
    
    def add_success(self, jar_name: str, file_count: int):
        """記錄成功提取"""
        self.success.append({'jar': jar_name, 'files': file_count})
    
    def add_warning(self, jar_name: str, reason: str):
        """記錄警告（例如：沒找到目標檔案）"""
        self.warnings.append({'jar': jar_name, 'reason': reason})
    
    def add_failure(self, jar_name: str, error: str):
        """記錄失敗（例如：讀取錯誤）"""
        self.failures.append({'jar': jar_name, 'error': error})
    
    def get_summary(self) -> Dict[str, Any]:
        """取得摘要統計"""
        return {
            'success_count': len(self.success),
            'warning_count': len(self.warnings),
            'failure_count': len(self.failures),
            'success': self.success,
            'warnings': self.warnings[:5],  # 只顯示前 5 個
            'failures': self.failures[:5],  # 只顯示前 5 個
        }


# --- 預覽報告生成 ---

def generate_preview_report(result: Dict[str, Any], mode: str, output_path: str) -> str:
    """
    生成預覽報告檔案
    
    Args:
        result: preview_extraction_generator 的結果
        mode: 'lang' 或 'book'
        output_path: 輸出資料夾路徑
    
    Returns:
        報告檔案的完整路徑
    """
    import datetime
    from pathlib import Path
    
    # 建立輸出資料夾
    output_dir = Path(output_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 生成報告檔名（含時間戳記）
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_filename = f"preview_report_{mode}_{timestamp}.md"
    report_path = output_dir / report_filename
    
    log.info(f"正在生成預覽報告：{report_filename}")
    
    # 準備報告內容
    preview_results = result.get('preview_results', [])
    total_jars = result.get('total_jars', 0)
    total_files = result.get('total_files', 0)
    total_size_mb = result.get('total_size_mb', 0)
    
    # 生成 Markdown 報告
    report_lines = [
        f"# JAR 提取預覽報告 - {mode.upper()}",
        f"",
        f"**生成時間：** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"",
        f"## 摘要統計",
        f"",
        f"- **總 JAR 數量：** {total_jars}",
        f"- **找到檔案數量：** {total_files}",
        f"- **有檔案的 JAR：** {len(preview_results)}",
        f"- **總大小：** {total_size_mb:.2f} MB",
        f"",
        f"## 詳細清單",
        f"",
    ]
    
    # 加入每個 JAR 的詳情
    for idx, r in enumerate(preview_results, 1):
        report_lines.append(f"### {idx}. {r['jar']}")
        report_lines.append(f"")
        report_lines.append(f"- **檔案數量：** {r['count']}")
        report_lines.append(f"- **JAR 大小：** {r['size_mb']:.2f} MB")
        report_lines.append(f"- **檔案清單：**")
        report_lines.append(f"")
        
        # 列出所有檔案（最多 50 個）
        for file_path in r['files'][:50]:
            report_lines.append(f"  - `{file_path}`")
        
        if len(r['files']) > 50:
            report_lines.append(f"  - ... 還有 {len(r['files']) - 50} 個檔案")
        
        report_lines.append(f"")
    
    # 寫入檔案
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(report_lines))
    
    # 記錄詳細資訊
    log.info(f"✅ 預覽報告生成成功")
    log.info(f"📄 檔案名稱：{report_filename}")
    log.info(f"📂 完整路徑：{report_path}")
    log.info(f"📊 統計摘要：找到 {total_files} 個檔案，來自 {len(preview_results)} 個 JAR，總大小 {total_size_mb:.2f} MB")
    
    return str(report_path)