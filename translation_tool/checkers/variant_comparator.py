# /minecraft_translator_flet/translation_tool/core/variant_comparator.py (新檔案)

import os
import orjson as json
import logging
from typing import Dict, Any, Generator
from opencc import OpenCC

# 導入我們自訂的工具
from ..utils.config_manager import config
from ..utils.text_processor import load_replace_rules, apply_replace_rules

log = logging.getLogger(__name__)

def compare_variants_generator(zh_cn_dir: str, zh_tw_dir: str, output_dir: str) -> Generator[Dict[str, Any], None, None]:
    """
    (核心功能) 比較 zh_cn 和 zh_tw 資料夾，找出翻譯不一致的條目。
    """
    os.makedirs(output_dir, exist_ok=True)
    yield {"progress": 0.0, "log": "開始比較簡繁翻譯差異..."}

    # 初始化 OpenCC 和規則
    try:
        converter = OpenCC('s2twp')
        rules = load_replace_rules(config.get("translation", {}).get("replace_rules_path", "replace_rules.json"))
    except Exception as e:
        yield {"progress": 1.0, "log": f"錯誤：初始化 OpenCC 或讀取規則失敗: {e}", "error": True}
        return

    cn_files = []
    for root, _, files in os.walk(zh_cn_dir):
        for file in files:
            if file.endswith('.json'):
                cn_files.append(os.path.join(root, file))
    
    total_files = len(cn_files)
    if total_files == 0:
        yield {"progress": 1.0, "log": f"在 '{zh_cn_dir}' 中未找到任何 zh_cn.json 檔案。", "error": True}
        return

    processed_count = 0
    total_diff_keys = 0
    files_with_diff = 0

    for cn_file_path in cn_files:
        processed_count += 1
        progress = processed_count / total_files
        
        try:
            rel_path = os.path.relpath(cn_file_path, zh_cn_dir)
            tw_file_path = os.path.join(zh_tw_dir, rel_path.replace('zh_cn.json', 'zh_tw.json'))
            
            if not os.path.exists(tw_file_path):
                yield {"progress": progress, "log": f"[跳過] 找不到對應的繁中檔案: {rel_path}"}
                continue

            with open(cn_file_path, 'rb') as f:
                cn_data = json.loads(f.read())
            with open(tw_file_path, 'rb') as f:
                tw_data = json.loads(f.read())

            common_keys = cn_data.keys() & tw_data.keys()
            report = {}

            for key in common_keys:
                if not isinstance(cn_data[key], str) or not isinstance(tw_data[key], str):
                    continue
                    
                cn_value = str(cn_data[key])
                tw_value = str(tw_data[key])
                
                # 執行與 ftb_translator 相同的轉換邏輯
                converted_cn_value = converter.convert(cn_value)
                converted_cn_value = apply_replace_rules(converted_cn_value, rules)
                
                if converted_cn_value != tw_value:
                    report[key] = {
                        "key": key,
                        "zh_cn_original": cn_value,
                        "zh_cn_converted_to_tw": converted_cn_value,
                        "zh_tw_actual": tw_value
                    }

            if report:
                files_with_diff += 1
                total_diff_keys += len(report)
                
                output_path = os.path.join(output_dir, rel_path)
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                with open(output_path, 'wb') as f:
                    f.write(json.dumps(report, option=json.OPT_INDENT_2 | json.OPT_APPEND_NEWLINE))
                
                yield {"progress": progress, "log": f"在 {rel_path} 中找到 {len(report)} 個翻譯差異。"}
            else:
                yield {"progress": progress, "log": f"比較 {rel_path} ... 一致。"}

        except Exception as e:
            yield {"progress": progress, "log": f"處理 {rel_path} 時出錯: {e}", "error": True}

    yield {"progress": 1.0, "log": f"--- 簡繁差異比較完成 ---"}
    yield {"progress": 1.0, "log": f"總共在 {files_with_diff} 個檔案中，找到 {total_diff_keys} 個翻譯差異。"}
    yield {"progress": 1.0, "log": f"報告已儲存至: {output_dir}"}