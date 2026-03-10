# translation_tool/checkers/english_residue_checker.py

import os
import json
import logging
import re
from typing import Dict, Any, Generator
from pathlib import Path

log = logging.getLogger(__name__)

# 英文檢查的正則 (來自 check_untranslated.py)
ENGLISH_PATTERN = re.compile(r'[A-Za-z]')

# 輔助函式：尋找 json 檔案
def find_json_files(directory: str):
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith('.json'):
                yield os.path.join(root, file)

def check_english_residue_generator(input_dir: str, output_dir: str) -> Generator[Dict[str, Any], None, None]:
    """
    (核心功能) 檢查翻譯檔案中是否殘留英文 (來自 check_untranslated.py 的功能重寫)。
    
    Args:
        input_dir (str): 要檢查的翻譯資料夾 (例如 zh_tw, zh_cn)。
        output_dir (str): 報告輸出資料夾。
    """
    os.makedirs(output_dir, exist_ok=True)
    yield {"progress": 0.0, "log": f"開始檢查殘留英文條目，目錄: {input_dir}"}

    json_files = list(find_json_files(input_dir))
    total_files = len(json_files)
    if total_files == 0:
        yield {"progress": 1.0, "log": f"在 '{input_dir}' 中未找到任何 json 檔案。", "error": True}
        return

    processed_count = 0
    total_suspicious = 0
    files_with_suspicious = 0

    for json_file in json_files:
        processed_count += 1
        progress = processed_count / total_files
        rel_path = os.path.relpath(json_file, input_dir)
        
        suspicious_entries = {}
        try:
            with open(json_file, encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            yield {"progress": progress, "log": f"[錯誤] 讀取 {rel_path} 失敗: {e}", "error": True}
            continue

        if not isinstance(data, dict):
            continue

        for key, value in data.items():
            if isinstance(value, str):
                if ENGLISH_PATTERN.search(value):
                    # NOTE: 原始的 check_untranslated.py 使用了 should_skip_lm_translation 
                    # 來排除像學名這類不應翻譯的詞語。您需要在程式碼環境中確保該函式被正確配置和調用。
                    # 這裡為了簡化並確保生成器能運作，暫時不包含 should_skip_lm_translation 的複雜依賴。
                    suspicious_entries[key] = value

        if suspicious_entries:
            total_suspicious += len(suspicious_entries)
            files_with_suspicious += 1
            
            # 儲存邏輯 (來自 check_untranslated.py)
            output_path = os.path.join(output_dir, rel_path)
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(suspicious_entries, f, ensure_ascii=False, indent=2)
            
            yield {"progress": progress, "log": f"在 {rel_path} 中找到 {len(suspicious_entries)} 個殘留英文條目。"}
        else:
            yield {"progress": progress, "log": f"檢查 {rel_path} ... OK。"}
            
    yield {"progress": 1.0, "log": f"--- 殘留英文檢查完成 ---"}
    yield {"progress": 1.0, "log": f"總共在 {files_with_suspicious} 個檔案中，找到 {total_suspicious} 個可疑殘留英文條目。"}
    yield {"progress": 1.0, "log": f"報告已儲存至: {output_dir}"}