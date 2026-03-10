# /minecraft_translator_flet/translation_tool/core/untranslated_checker.py (新檔案)

import os
import orjson as json
import logging
from typing import Dict, Any, Generator

log = logging.getLogger(__name__)

def check_untranslated_generator(en_us_dir: str, zh_tw_dir: str, output_dir: str) -> Generator[Dict[str, Any], None, None]:
    """
    (核心功能) 比較 en_us 和 zh_tw 資料夾，找出未翻譯的條目。
    """
    os.makedirs(output_dir, exist_ok=True)
    yield {"progress": 0.0, "log": "開始檢查未翻譯條目..."}

    en_files = []
    for root, _, files in os.walk(en_us_dir):
        for file in files:
            if file.endswith('.json'):
                en_files.append(os.path.join(root, file))
    
    total_files = len(en_files)
    if total_files == 0:
        yield {"progress": 1.0, "log": f"在 '{en_us_dir}' 中未找到任何 en_us.json 檔案。", "error": True}
        return

    processed_count = 0
    total_untranslated_keys = 0
    files_with_missing = 0

    for en_file_path in en_files:
        processed_count += 1
        progress = processed_count / total_files
        
        try:
            rel_path = os.path.relpath(en_file_path, en_us_dir)
            tw_file_path = os.path.join(zh_tw_dir, rel_path)
            
            with open(en_file_path, 'rb') as f:
                en_data = json.loads(f.read())
            
            if not os.path.exists(tw_file_path):
                yield {"progress": progress, "log": f"[警告] 找不到對應的繁中檔案: {rel_path}，將整個檔案標記為未翻譯。"}
                output_path = os.path.join(output_dir, rel_path)
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                with open(output_path, 'wb') as f:
                    f.write(json.dumps(en_data, option=json.OPT_INDENT_2))
                total_untranslated_keys += len(en_data)
                files_with_missing += 1
                continue

            with open(tw_file_path, 'rb') as f:
                tw_data = json.loads(f.read())

            untranslated_keys = en_data.keys() - tw_data.keys()
            
            if untranslated_keys:
                files_with_missing += 1
                report = {key: en_data[key] for key in untranslated_keys}
                total_untranslated_keys += len(report)
                
                output_path = os.path.join(output_dir, rel_path)
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                with open(output_path, 'wb') as f:
                    f.write(json.dumps(report, option=json.OPT_INDENT_2))
                
                yield {"progress": progress, "log": f"在 {rel_path} 中找到 {len(report)} 個未翻譯條目。"}
            else:
                yield {"progress": progress, "log": f"檢查 {rel_path} ... OK。"}

        except Exception as e:
            yield {"progress": progress, "log": f"處理 {rel_path} 時出錯: {e}", "error": True}

    yield {"progress": 1.0, "log": f"--- 未翻譯檢查完成 ---"}
    yield {"progress": 1.0, "log": f"總共在 {files_with_missing} 個檔案中，找到 {total_untranslated_keys} 個未翻譯的條目。"}
    yield {"progress": 1.0, "log": f"報告已儲存至: {output_dir}"}