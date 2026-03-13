"""translation_tool/checkers/variant_comparator_tsv.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

# translation_tool/checkers/variant_comparator_tsv.py

import os
import pandas as pd
from opencc import OpenCC
from typing import Dict, Any, Generator
import logging

log = logging.getLogger(__name__)

def compare_variants_tsv_generator(file_path: str, output_file: str) -> Generator[Dict[str, Any], None, None]:
    """
    (核心功能) 比較 TSV 檔案中 'zh_cn' 和 'zh_tw' 欄位的差異 (來自 compare_zh_variants.py)。
    """
    yield {"progress": 0.0, "log": f"開始比較 TSV 檔案: {os.path.basename(file_path)}"}

    if not os.path.exists(file_path):
        yield {"progress": 1.0, "log": f"錯誤：檔案 '{file_path}' 不存在。", "error": True}
        return

    try:
        # 讀取 TSV 檔案
        # 保持與舊檔案 compare_zh_variants.py 的讀取邏輯一致
        df = pd.read_csv(file_path, sep='\t', encoding='utf-8') 
    except Exception as e:
        yield {"progress": 1.0, "log": f"錯誤：讀取檔案失敗：{e}", "error": True}
        return

    required_columns = ['key', 'zh_cn', 'zh_tw']
    if not all(col in df.columns for col in required_columns):
        yield {"progress": 1.0, "log": f"錯誤：檔案缺少必要的欄位。需要：{required_columns}", "error": True}
        return

    try:
        # 初始化 OpenCC 轉換器 (s2twp.json 在新版 OpenCC 中改為 's2twp')
        converter = OpenCC('s2twp') 
    except Exception as e:
        yield {"progress": 1.0, "log": f"錯誤：初始化 OpenCC 失敗: {e}", "error": True}
        return

    differences = []
    total_rows = len(df)
    
    yield {"progress": 0.0, "log": f"找到 {total_rows} 條記錄，開始逐條比對..."}

    for index, row in df.iterrows():
        progress = (index + 1) / total_rows
        
        try:
            key = row['key']
            zh_cn_original = str(row['zh_cn']) if pd.notna(row['zh_cn']) else ""
            zh_tw_original = str(row['zh_tw']) if pd.notna(row['zh_tw']) else ""

            zh_cn_converted = converter.convert(zh_cn_original)
            
            if zh_cn_converted != zh_tw_original:
                differences.append({
                    'key': key,
                    'zh_cn_original': zh_cn_original,
                    'zh_cn_converted_by_opencc': zh_cn_converted,
                    'zh_tw_original': zh_tw_original
                })
                yield {"progress": progress, "log": f"[差異] Key: {key}"}
            
            if (index + 1) % 100 == 0 or (index + 1) == total_rows:
                 yield {"progress": progress, "log": f"已處理 {index + 1}/{total_rows} 條記錄。"}

        except Exception as e:
             yield {"progress": progress, "log": f"處理第 {index + 1} 行時出錯: {e}", "error": True}

    if differences:
        total_diff = len(differences)
        yield {"progress": 1.0, "log": f"\n--- 簡繁 TSV 比較完成 ---"}
        yield {"progress": 1.0, "log": f"總共發現 {total_diff} 條差異條目。"}
        
        diff_df = pd.DataFrame(differences)
        try:
            os.makedirs(os.path.dirname(output_file) or '.', exist_ok=True)
            diff_df.to_csv(output_file, index=False, encoding='utf-8-sig')
            yield {"progress": 1.0, "log": f"差異結果已儲存到：'{output_file}'"}
        except Exception as e:
            yield {"progress": 1.0, "log": f"錯誤：儲存結果到 '{output_file}' 時發生問題：{e}", "error": True}
    else:
        yield {"progress": 1.0, "log": "\n未發現簡繁差異。"}