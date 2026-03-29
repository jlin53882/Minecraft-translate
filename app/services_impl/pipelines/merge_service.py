"""Merge pipeline service wrappers.

PR20：將 merge 類 service 從 app.services.py 抽離到 pipelines 子模組，
由 app.services 持續做 façade / re-export，維持 UI import 相容。
"""

from __future__ import annotations

import logging
import os
import traceback
from pathlib import Path

from app.services_impl.logging_service import UI_LOG_HANDLER
from app.services_impl.pipelines._pipeline_logging import ensure_pipeline_logging
from translation_tool.core.lang_merger import merge_zhcn_to_zhtw_from_zip

logger = logging.getLogger(__name__)

def run_merge_zip_batch_service(
    zip_paths: list[str],
    output_dir: str,
    session,
    only_process_lang,
):
    """
    以 ZIP 為單位進行合併（支援 generator merge）
    - ZIP 層級 progress
    - merge_zhcn_to_zhtw_from_zip 內部 progress 疊加
    - log / error 完整轉交給 session
    """
    # ⭐ 每次任務開始，都重新讀取一次 config 並設定 Logger
    ensure_pipeline_logging()
    UI_LOG_HANDLER.set_session(session)

    # ── 收集失敗 ZIP（繼續處理後續檔案）─────────────────────────────
    failed_zips: list[dict] = []

    try:
        total = len(zip_paths)
        if total == 0:
            session.add_log("[系統] 未選擇任何 ZIP 檔案")
            session.finish()
            return

        for idx, zip_path in enumerate(zip_paths):
            zip_name = Path(zip_path).name
            zip_base_progress = idx / total

            session.add_log(f"[ZIP {idx + 1}/{total}] 開始處理：{zip_name}")

            try:
                # ⚠️ 關鍵：一定要 iterate generator，否則 merge 不會執行
                for update in merge_zhcn_to_zhtw_from_zip(zip_path, output_dir, only_process_lang):
                    # ---- log ----
                    if "log" in update and update["log"]:
                        session.add_log(update["log"])

                    # ---- progress（疊加 ZIP 進度）----
                    if "progress" in update and update["progress"] is not None:
                        merged_progress = zip_base_progress + (update["progress"] / total)
                        session.set_progress(min(merged_progress, 0.999))

                    # ---- error ----
                    if update.get("error"):
                        session.set_error()
                        failed_zips.append({"name": zip_name, "reason": update["error"]})
                        session.add_log(f"[ZIP {idx + 1}/{total}] 失敗：{zip_name}（{update['error']}）")
                        break  # 此 ZIP 失敗，繼續下一個

                else:
                    # for...else 表示正常完成（未 break）
                    session.add_log(f"[ZIP {idx + 1}/{total}] 完成：{zip_name}")

            except Exception as e:
                tb = traceback.format_exc()
                logger.error(f"[ZIP {idx + 1}/{total}] 錯誤：{zip_name}\n{e}\n{tb}")
                session.add_log(f"[ZIP {idx + 1}/{total}] 錯誤：{zip_name}\n{e}\n{tb}")
                failed_zips.append({"name": zip_name, "reason": str(e)})

            # ZIP 完成後，至少推進一次 progress
            session.set_progress((idx + 1) / total)

        # ── 輸出目錄掃描統計 ─────────────────────────────────────
        def _count_output_files(out_dir: str) -> dict:
            """掃描各輸出子目錄的檔案數量。"""
            result = {
                "lang_output": 0,
                "待翻譯": 0,
                "patchouli_output": 0,
                "other_output": 0,
                "errordata_output": 0,
            }
            if not os.path.exists(out_dir):
                return result
            for root, dirs, files in os.walk(out_dir):
                rel = os.path.relpath(root, out_dir)
                if rel.startswith("lang_output"):
                    if "待翻譯" in rel:
                        result["待翻譯"] += len(files)
                    else:
                        result["lang_output"] += len(files)
                elif rel.startswith("patchouli_output"):
                    result["patchouli_output"] += len(files)
                elif rel.startswith("other_output"):
                    result["other_output"] += len(files)
                elif rel.startswith("errordata_output"):
                    result["errordata_output"] += len(files)
            return result

        output_counts = _count_output_files(output_dir)

        # 附加 summary 至 session（供 UI 讀取）
        session.summary = {
            "failed_zips": failed_zips,
            "output_counts": output_counts,
        }

        session.finish()

    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"[致命錯誤] ZIP 合併失敗：{e}\n{tb}")
        session.add_log(f"[致命錯誤] ZIP 合併失敗：{e}\n{tb}")
        session.set_error()

    finally:
        # ⭐ 避免 handler 留著舊 session
        UI_LOG_HANDLER.set_session(None)
