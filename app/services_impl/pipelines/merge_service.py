"""Merge pipeline service wrappers.

PR20：將 merge 類 service 從 app.services.py 抽離到 pipelines 子模組，
由 app.services 持續做 façade / re-export，維持 UI import 相容。
"""

from __future__ import annotations

import logging
import traceback
from pathlib import Path

from app.services_impl.logging_service import UI_LOG_HANDLER
from app.services_impl.pipelines._pipeline_logging import ensure_pipeline_logging
from translation_tool.core.lang_merger import merge_zhcn_to_zhtw_from_zip, merge_zhcn_to_zhtw_from_folder
from translation_tool.core.lang_merge_extracted_assets import merge_extracted_to_assets
from translation_tool.utils.config_manager import load_config

import os

logger = logging.getLogger(__name__)

def run_merge_zip_batch_service(
    zip_paths: list[str],
    output_dir: str,
    session,
    only_process_lang,
    process_zh_cn: bool | None = None,
    patchouli_skip: bool | None = None,
    patchouli_threshold: float | None = None,
    zh_en_threshold: int | None = None,
):
    """
    以 ZIP 為單位進行合併（支援 generator merge）
    - ZIP 層級 progress
    - merge_zhcn_to_zhtw_from_zip 內部 progress 疊加
    - log / error 完整轉交給 session
    - 批次完成時 yield 統計摘要
    """
    # ⭐ 每次任務開始，都重新讀取一次 config 並設定 Logger
    ensure_pipeline_logging()
    UI_LOG_HANDLER.set_session(session)

    # 統計計數器
    stats = {
        "total_zips": len(zip_paths),
        "success_zips": 0,
        "failed_zips": 0,
        "errored_files": 0,
        "failed_zips_list": [],  # [{"name": str, "error": str}, ...]
    }

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

    try:
        total = len(zip_paths)
        if total == 0:
            session.add_log("[系統] 未選擇任何 ZIP 檔案")
            session.finish()
            yield {
                "progress": 1.0,
                "log": None,
                "summary": stats,
            }
            return

        for idx, zip_path in enumerate(zip_paths):
            zip_name = Path(zip_path).name
            zip_base_progress = idx / total

            session.add_log(f"[ZIP {idx + 1}/{total}] 開始處理：{zip_name}")

            try:
                # ⚠️ 關鍵：一定要 iterate generator，否則 merge 不會執行
                for update in merge_zhcn_to_zhtw_from_zip(zip_path, output_dir, only_process_lang,
                                                process_zh_cn=process_zh_cn,
                                                patchouli_skip=patchouli_skip,
                                                patchouli_threshold=patchouli_threshold,
                                                zh_en_threshold=zh_en_threshold):
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
                        return

                session.add_log(f"[ZIP {idx + 1}/{total}] 完成：{zip_name}")
                stats["success_zips"] += 1

            except Exception as e:
                tb = traceback.format_exc()
                logger.error(f"[ZIP {idx + 1}/{total}] 錯誤：{zip_name}\n{e}\n{tb}")
                session.add_log(f"[ZIP {idx + 1}/{total}] 錯誤：{zip_name}\n{e}\n{tb}")
                stats["failed_zips"] += 1
                stats["failed_zips_list"].append({"name": zip_name, "error": str(e)})
                # 不再 return，繼續處理下一個 ZIP

            # ZIP 完成後，至少推進一次 progress
            session.set_progress((idx + 1) / total)

        # 產出統計摘要，並寫入 session 供 UI 取用
        output_counts = _count_output_files(output_dir)
        final_summary = {
            "total_zips": stats["total_zips"],
            "success_zips": stats["success_zips"],
            "failed_zips": stats["failed_zips"],
            "errored_files": stats["errored_files"],
            "failed_zips_list": stats["failed_zips_list"],
            "output_counts": output_counts,
        }
        session.set_summary(final_summary)
        yield {"progress": 1.0, "log": None, "summary": final_summary}
        session.finish()

    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"[致命錯誤] ZIP 合併失敗：{e}\n{tb}")
        session.add_log(f"[致命錯誤] ZIP 合併失敗：{e}\n{tb}")
        # 產出統計摘要（即使失敗也要回報）
        error_summary = {
            "total_zips": stats["total_zips"],
            "success_zips": stats["success_zips"],
            "failed_zips": stats["failed_zips"],
            "errored_files": stats["errored_files"],
            "failed_zips_list": stats["failed_zips_list"],
            "output_counts": _count_output_files(output_dir),
        }
        session.set_summary(error_summary)
        yield {"progress": 1.0, "log": None, "summary": error_summary}
        session.set_error()

    finally:
        # ⭐ 避免 handler 留著舊 session
        UI_LOG_HANDLER.set_session(None)


def run_merge_folder_batch_service(
    input_dir: str,
    output_dir: str,
    session,
    only_process_lang,
    process_zh_cn: bool | None = None,
    patchouli_skip: bool | None = None,
    patchouli_threshold: float | None = None,
    zh_en_threshold: int | None = None,
):
    """以資料夾為單位進行合併（支援 generator merge）。

    與 run_merge_zip_batch_service 結構相同，但使用 merge_zhcn_to_zhtw_from_folder。
    """
    ensure_pipeline_logging()
    UI_LOG_HANDLER.set_session(session)

    stats = {
        "total_folders": 1,
        "success_folders": 0,
        "failed_folders": 0,
        "errored_files": 0,
        "failed_folders_list": [],
    }

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

    try:
        session.add_log(f"[資料夾] 開始處理：{os.path.basename(input_dir)}")

        try:
            for update in merge_zhcn_to_zhtw_from_folder(
                input_dir,
                output_dir,
                only_process_lang,
                process_zh_cn=process_zh_cn,
                patchouli_skip=patchouli_skip,
                patchouli_threshold=patchouli_threshold,
                zh_en_threshold=zh_en_threshold,
            ):
                if "log" in update and update["log"]:
                    session.add_log(update["log"])

                if "progress" in update and update["progress"] is not None:
                    session.set_progress(update["progress"])

                if update.get("error"):
                    session.set_error()
                    return

            session.add_log(f"[資料夾] 完成：{os.path.basename(input_dir)}")
            stats["success_folders"] += 1

            # 階段 2 (2026-08-02 PR-XX merge-asset-integration):
            # 從 input_dir 內 XX_extracted/ 的 lang 檔 key-by-key 合併進
            # output_dir/lang_output/assets/{modid}/lang/{xx_yy}.json
            # config flag "enable_extracted_to_assets_merge" 控制是否跑。
            try:
                cfg = load_config()
                enable_extracted_merge = cfg.get(
                    "lang_merger", {}
                ).get("enable_extracted_to_assets_merge", True)
                if enable_extracted_merge:
                    session.add_log("[階段2] 合併 XX_extracted → assets/")
                    lang_output_dir = os.path.join(output_dir, "lang_output")
                    for update in merge_extracted_to_assets(
                        lang_output_dir=lang_output_dir,
                        session=session,
                    ):
                        if "log" in update and update["log"]:
                            session.add_log(update["log"])
                        if (
                            "progress" in update
                            and update["progress"] is not None
                        ):
                            session.set_progress(update["progress"])
                        if update.get("error"):
                            session.add_log(
                                "[階段2] 錯誤,assets 合併中止"
                            )
                            return
                    session.add_log("[階段2] 完成")
            except Exception as stage2_err:
                logger.warning(f"[階段2] 錯誤(不中断階段1): {stage2_err}")
                session.add_log(f"[階段2] 錯誤: {stage2_err}")

        except Exception as e:
            tb = traceback.format_exc()
            logger.error(f"[資料夾] 錯誤：{input_dir}\n{e}\n{tb}")
            session.add_log(f"[資料夾] 錯誤：{input_dir}\n{e}\n{tb}")
            stats["failed_folders"] += 1
            stats["failed_folders_list"].append({"name": os.path.basename(input_dir), "error": str(e)})

        output_counts = _count_output_files(output_dir)
        final_summary = {
            "total_folders": stats["total_folders"],
            "success_folders": stats["success_folders"],
            "failed_folders": stats["failed_folders"],
            "errored_files": stats["errored_files"],
            "failed_folders_list": stats["failed_folders_list"],
            "output_counts": output_counts,
        }
        session.set_summary(final_summary)
        yield {"progress": 1.0, "log": None, "summary": final_summary}
        session.finish()

    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"[致命錯誤] 資料夾合併失敗：{e}\n{tb}")
        session.add_log(f"[致命錯誤] 資料夾合併失敗：{e}\n{tb}")
        error_summary = {
            "total_folders": stats["total_folders"],
            "success_folders": stats["success_folders"],
            "failed_folders": stats["failed_folders"],
            "errored_files": stats["errored_files"],
            "failed_folders_list": stats["failed_folders_list"],
            "output_counts": _count_output_files(output_dir),
        }
        session.set_summary(error_summary)
        yield {"progress": 1.0, "log": None, "summary": error_summary}
        session.set_error()

    finally:
        UI_LOG_HANDLER.set_session(None)
