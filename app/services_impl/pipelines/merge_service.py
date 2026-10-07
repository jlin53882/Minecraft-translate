"""Merge pipeline service wrappers.

PR20：將 merge 類 service 從 app.services.py 抽離到 pipelines 子模組，
由 app.services 持續做 façade / re-export，維持 UI import 相容。
"""

from __future__ import annotations

import logging
import os
import shutil
import traceback
from pathlib import Path

from app.services_impl.logging_service import UI_LOG_HANDLER
from app.services_impl.pipelines._pipeline_logging import (
    ensure_pipeline_logging,
    mirror_session_log,
)
from app.services_impl.pipelines.output_lease import acquire_output_lease
from translation_tool.core.lang_merge_extracted_assets import merge_extracted_to_assets
from translation_tool.core.lang_merger import (
    merge_zhcn_to_zhtw_from_folder,
    merge_zhcn_to_zhtw_from_zip,
)
from translation_tool.utils.cancellation import TaskCancelled, raise_if_cancelled
from translation_tool.utils.config_manager import load_config

logger = logging.getLogger(__name__)


def _raise_if_session_cancelled(session) -> None:
    """合併服務自己的取消檢查點：session 旗標（合併頁取消）或 cancel_scope（流水線取消）。"""
    raise_if_cancelled()
    if getattr(session, "cancel_requested", False) is True:
        raise TaskCancelled()


def _cleanup_cancelled_output(
    output_dir: str, existed_before: bool, finished: bool, session
) -> None:
    """取消時移除本次新建的合併輸出（半成品）；不碰使用者原本就存在的目錄。"""
    if finished or existed_before or not getattr(session, "cancel_requested", False):
        return
    try:
        shutil.rmtree(output_dir)
    except FileNotFoundError:
        return  # 已經不在了
    except OSError as exc:
        # 檔案被鎖住、防毒、權限：不能回報「已清理」
        _session_log(
            session, f"[取消] 清理半成品輸出失敗：{output_dir}；{exc!r}", "warning"
        )
        return
    if os.path.exists(output_dir):
        _session_log(session, f"[取消] 半成品輸出未完全清除：{output_dir}", "warning")
    else:
        _session_log(session, f"[取消] 已清理半成品輸出：{output_dir}", "info")


def _claim_output(output_dir: str):
    """取得輸出資料夾的獨占租約，並在租約內記錄它原本存不存在（見 output_lease.py）。"""
    lease = acquire_output_lease(output_dir)
    return lease, os.path.exists(output_dir)


def _release_merge_output(output_dir, existed_before, finished, session, lease) -> None:
    """取消時清掉新建的輸出，**清完才**釋放租約（別的任務不會在清理中途闖進來）。"""
    try:
        _cleanup_cancelled_output(output_dir, existed_before, finished, session)
    finally:
        if lease is not None:
            lease.release()


def _session_log(session, text: str, level: str = "info") -> None:
    mirror_session_log(session, logger, text, level)


def _count_output_files(out_dir: str) -> dict:
    """掃描各輸出子目錄的檔案數量。2026-08-04: 抽到 module-level 共用。"""
    result = {
        "lang_output": 0,
        "assets": 0,
        "待翻譯": 0,
        "patchouli_output": 0,
        "other_output": 0,
        "errordata_output": 0,
    }
    if not os.path.exists(out_dir):
        return result
    for root, dirs, files in os.walk(out_dir):
        # Windows 的 relpath 用反斜線，統一成 "/" 再比對
        rel = os.path.relpath(root, out_dir).replace("\\", "/")
        if rel.startswith("lang_output/assets"):
            result["assets"] += len(files)
        elif rel.startswith("lang_output"):
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


def _soft_error_detail(update: dict, default: str = "內部軟性錯誤") -> str:
    """從 generator update 保留可用的錯誤訊息。"""
    for key in ("error", "message", "log"):
        value = update.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default


def _zip_summary(stats: dict, output_dir: str) -> dict:
    """ZIP 批次的統計摘要（含輸出檔案計數）。"""
    return {
        "total_zips": stats["total_zips"],
        "success_zips": stats["success_zips"],
        "failed_zips": stats["failed_zips"],
        "errored_files": stats["errored_files"],
        "failed_zips_list": stats["failed_zips_list"],
        "output_counts": _count_output_files(output_dir),
    }


def _folder_summary(stats: dict, output_dir: str) -> dict:
    """資料夾批次的統計摘要（含輸出檔案計數）。"""
    return {
        "total_folders": stats["total_folders"],
        "success_folders": stats["success_folders"],
        "failed_folders": stats["failed_folders"],
        "errored_files": stats["errored_files"],
        "failed_folders_list": stats["failed_folders_list"],
        "output_counts": _count_output_files(output_dir),
    }


def _record_zip_result(
    stats: dict, session, idx: int, total: int, zip_name: str, zip_errors: list[str]
) -> None:
    """依單一 ZIP 的錯誤清單更新統計並寫入 session 日誌。"""
    if zip_errors:
        stats["failed_zips"] += 1
        stats["failed_zips_list"].append(
            {
                "name": zip_name,
                "error": "; ".join(dict.fromkeys(zip_errors)),
            }
        )
        _session_log(session, f"[ZIP {idx + 1}/{total}] 失敗：{zip_name}", "error")
    else:
        _session_log(session, f"[ZIP {idx + 1}/{total}] 完成：{zip_name}")
        stats["success_zips"] += 1


def _record_folder_result(
    stats: dict, input_dir: str, folder_errors: list[str]
) -> None:
    """依資料夾的錯誤清單更新統計。"""
    if folder_errors:
        stats["failed_folders"] = 1
        stats["failed_folders_list"].append(
            {
                "name": os.path.basename(input_dir),
                "error": "; ".join(dict.fromkeys(folder_errors)),
            }
        )
    else:
        stats["success_folders"] = 1


def _merge_one_zip(
    zip_path: str,
    output_dir: str,
    session,
    only_process_lang,
    idx: int,
    total: int,
    zip_name: str,
    zip_base_progress: float,
    *,
    process_zh_cn,
    patchouli_skip,
    patchouli_threshold,
    zh_en_threshold,
    use_translation_db=None,
    translation_db_version=None,
) -> list[str]:
    """合併單一 ZIP（把進度疊加到整批進度），回傳這個 ZIP 的錯誤訊息清單。"""
    zip_errors: list[str] = []
    try:
        # ⚠️ 關鍵：一定要 iterate generator，否則 merge 不會執行
        for update in merge_zhcn_to_zhtw_from_zip(
            zip_path,
            output_dir,
            only_process_lang,
            process_zh_cn=process_zh_cn,
            patchouli_skip=patchouli_skip,
            patchouli_threshold=patchouli_threshold,
            zh_en_threshold=zh_en_threshold,
            use_translation_db=use_translation_db,
            translation_db_version=translation_db_version,
        ):
            _raise_if_session_cancelled(session)  # 取消檢查點：每個 update
            # ---- log ----
            if update.get("log"):
                session.add_log(update["log"])

            # ---- progress（疊加 ZIP 進度）----
            if "progress" in update and update["progress"] is not None:
                merged_progress = zip_base_progress + (update["progress"] / total)
                session.set_progress(min(merged_progress, 0.999))

            # ---- error ----
            # 2026-08-04 修正 A2: 軟性 error 不中止整批,繼續處理下一個 ZIP
            if update.get("error"):
                zip_errors.append(_soft_error_detail(update))

    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc()
        _session_log(
            session, f"[ZIP {idx + 1}/{total}] 錯誤：{zip_name}\n{e!r}\n{tb}", "error"
        )
        zip_errors.append(str(e))
    return zip_errors


def _fatal_zip_error(session, stats: dict, output_dir: str, error: Exception) -> dict:
    """ZIP 合併的致命錯誤：記錄並寫入摘要（即使失敗也要回報）；``set_error``／finish 由呼叫端處理。"""
    tb = traceback.format_exc()
    _session_log(session, f"[致命錯誤] ZIP 合併失敗：{error!r}\n{tb}", "error")
    error_summary = _zip_summary(stats, output_dir)
    session.set_summary(error_summary)
    return error_summary


def run_merge_zip_batch_service(
    zip_paths: list[str],
    output_dir: str,
    session,
    only_process_lang,
    process_zh_cn: bool | None = None,
    patchouli_skip: bool | None = None,
    patchouli_threshold: float | None = None,
    zh_en_threshold: int | None = None,
    use_translation_db: bool | None = None,
    translation_db_version: str | None = None,
):
    """以 ZIP 為單位合併，逐 ZIP 回報進度、日誌與統計摘要。"""
    # ⭐ 每次任務開始，都重新讀取一次 config 並設定 Logger
    ensure_pipeline_logging()
    UI_LOG_HANDLER.set_session(session)
    output_existed_before, lease = True, None
    stats = {  # 統計計數器
        "total_zips": len(zip_paths),
        "success_zips": 0,
        "failed_zips": 0,
        "errored_files": 0,
        "failed_zips_list": [],  # [{"name": str, "error": str}, ...]
    }

    finished = False  # generator 被 close（取消）時由 finally 補 finish
    try:
        lease, output_existed_before = _claim_output(output_dir)  # 被占用 → 錯誤路徑
        total = len(zip_paths)
        if total == 0:
            _session_log(session, "[系統] 未選擇任何 ZIP 檔案", "warning")
            session.finish()
            finished = True
            yield {
                "progress": 1.0,
                "log": None,
                "summary": stats,
            }
            return

        for idx, zip_path in enumerate(zip_paths):
            _raise_if_session_cancelled(session)  # 取消檢查點：ZIP 與 ZIP 之間
            zip_name = Path(zip_path).name
            zip_base_progress = idx / total

            _session_log(session, f"[ZIP {idx + 1}/{total}] 開始處理：{zip_name}")

            zip_errors = _merge_one_zip(
                zip_path,
                output_dir,
                session,
                only_process_lang,
                idx,
                total,
                zip_name,
                zip_base_progress,
                process_zh_cn=process_zh_cn,
                patchouli_skip=patchouli_skip,
                patchouli_threshold=patchouli_threshold,
                zh_en_threshold=zh_en_threshold,
                use_translation_db=use_translation_db,
                translation_db_version=translation_db_version,
            )

            _record_zip_result(stats, session, idx, total, zip_name, zip_errors)

            # ZIP 完成後，至少推進一次 progress
            session.set_progress((idx + 1) / total)

        # 產出統計摘要，並寫入 session 供 UI 取用
        final_summary = _zip_summary(stats, output_dir)
        session.set_summary(final_summary)
        yield {"progress": 1.0, "log": None, "summary": final_summary}
        # 部分失敗維持批次語意（DONE + failed_zips_list）；全部失敗才視為任務失敗，
        # 避免呼叫端（例如一鍵流程）把「沒有任何 ZIP 成功」當成完成。
        if stats["success_zips"] == 0:
            _session_log(session, "[系統] 所有 ZIP 皆處理失敗", "error")
            session.set_error()
            session.finish()  # ERROR 也要 finish，TaskManager 才會離開 active
        else:
            session.finish()
        finished = True

    except Exception as e:  # noqa: BLE001
        error_summary = _fatal_zip_error(session, stats, output_dir, e)
        yield {"progress": 1.0, "log": None, "summary": error_summary}
        session.set_error()
        session.finish()
        finished = True

    finally:
        try:
            _release_merge_output(
                output_dir, output_existed_before, finished, session, lease
            )
        finally:
            UI_LOG_HANDLER.set_session(None)
            if not finished:
                session.finish()


def _set_monotonic_progress(session, value: float) -> None:
    """進度只增不減（兩個階段／多個來源共用同一個 session 時不會倒退）。"""
    snapshot_fn = getattr(session, "snapshot", None)
    if callable(snapshot_fn):
        current = snapshot_fn().get("progress", 0.0)
    else:
        current = getattr(session, "progress", 0.0)
    session.set_progress(max(float(current or 0.0), min(1.0, value)))


def _run_folder_stage1(
    input_dir: str,
    output_dir: str,
    session,
    only_process_lang,
    options: dict,
    progress_start: float,
    progress_end: float,
    folder_errors: list[str],
) -> None:
    """階段 1：zh_cn → zh_tw（佔進度區間的前 90%）；軟性 error 不中止，記入 folder_errors。"""
    for update in merge_zhcn_to_zhtw_from_folder(
        input_dir,
        output_dir,
        only_process_lang,
        **options,
        progress_start=progress_start,
        progress_end=progress_start + (progress_end - progress_start) * 0.90,
    ):
        # 取消檢查點：每個 update 之後（停止消費即中止核心 generator 的後續處理）
        _raise_if_session_cancelled(session)
        if update.get("log"):
            session.add_log(update["log"])
        if "progress" in update and update["progress"] is not None:
            _set_monotonic_progress(session, update["progress"])
        # 2026-08-04 修正 A2: 軟性 error 不中止,繼續處理
        if update.get("error"):
            folder_errors.append(_soft_error_detail(update))


def _run_extracted_stage2(
    folder_errors: list[str],
    output_dir: str,
    session,
    progress_start: float,
    progress_end: float,
    db_options: dict | None = None,
) -> None:
    """階段 2：把 XX_extracted 的 lang 檔 key-by-key 合併進 lang_output/assets（就地追加 folder_errors）。

    階段 1 已有錯誤時略過；由設定 ``lang_merger.enable_extracted_to_assets_merge`` 控制是否執行。
    進度落在 ``progress_start~progress_end`` 區間的最後 10%（單調遞增）。
    """
    # 階段 2 (2026-08-02 PR-XX merge-asset-integration):
    # 從 input_dir 內 XX_extracted/ 的 lang 檔 key-by-key 合併進
    # output_dir/lang_output/assets/{modid}/lang/{xx_yy}.json
    if folder_errors:
        _session_log(
            session, "[階段 2/2 略過] 階段 1 發生錯誤，不處理部分輸出", "warning"
        )
        return
    try:
        cfg = load_config()
        lang_merger = cfg.get("lang_merger", {})
        if not lang_merger.get("enable_extracted_to_assets_merge", True):
            _session_log(session, "[階段 2/2 略過] 檔案合併(階段 2) 未啟用，跳過")
            return
        _session_log(session, "[階段 2/2 開始] XX_extracted → assets 合併")
        lang_output_dir = os.path.join(output_dir, "lang_output")
        stage2_start = progress_start + (progress_end - progress_start) * 0.90
        for update in merge_extracted_to_assets(
            lang_output_dir=lang_output_dir,
            session=session,
            pending_folder_names=(
                lang_merger.get("pending_folder_name", "待翻譯"),
                lang_merger.get("pending_organized_folder_name", "待翻譯整理需翻譯"),
            ),
            **(db_options or {}),
        ):
            _raise_if_session_cancelled(session)  # 取消檢查點：階段 2 每個 update
            if update.get("log"):
                session.add_log(update["log"])
            if "progress" in update and update["progress"] is not None:
                _set_monotonic_progress(
                    session,
                    stage2_start + update["progress"] * (progress_end - stage2_start),
                )
            if update.get("error"):
                folder_errors.append(_soft_error_detail(update))
                _session_log(session, "[階段 2/2 錯誤] assets 合併中止", "error")
                break
        if not folder_errors:
            _session_log(session, "[階段 2/2 完成]")
    except Exception as stage2_err:  # noqa: BLE001
        _session_log(session, f"[階段 2/2 錯誤]: {stage2_err!r}", "error")
        folder_errors.append(str(stage2_err))


def _finish_folder_run(session, stats: dict, output_dir: str, failed: bool) -> dict:
    """寫入摘要並在失敗時標記 session；回傳最後一筆更新（成功時的 finish 由呼叫端在 yield 後執行）。"""
    final_summary = _folder_summary(stats, output_dir)
    session.set_summary(final_summary)
    if failed:
        session.set_error()
    return {"progress": 1.0, "log": None, "error": failed, "summary": final_summary}


def _fatal_folder_error(
    session, stats: dict, output_dir: str, error: Exception
) -> dict:
    """資料夾合併的致命錯誤：記錄、寫入摘要並 ``set_error()``；回傳摘要（finish 由呼叫端決定）。"""
    tb = traceback.format_exc()
    _session_log(session, f"[致命錯誤] 資料夾合併失敗：{error!r}\n{tb}", "error")
    error_summary = _folder_summary(stats, output_dir)
    session.set_summary(error_summary)
    session.set_error()
    return error_summary


def _run_folder_stages(
    input_dir: str,
    output_dir: str,
    session,
    only_process_lang,
    options: dict,
    progress_start: float,
    progress_end: float,
    folder_errors: list[str],
) -> None:
    """階段 1（zh_cn → zh_tw）與階段 2（XX_extracted → assets），錯誤累積在 folder_errors。"""
    _run_folder_stage1(
        input_dir,
        output_dir,
        session,
        only_process_lang,
        options,
        progress_start,
        progress_end,
        folder_errors,
    )

    if folder_errors:
        _session_log(
            session,
            "[階段 1/2 失敗] zh_cn → zh_tw 處理發生錯誤："
            + "；".join(dict.fromkeys(folder_errors)),
            "error",
        )
    else:
        _session_log(session, f"[資料夾] 完成：{os.path.basename(input_dir)}")
        _session_log(session, "[階段 1/2 完成] zh_cn → zh_tw 翻譯已完成")

    # 階段 2 (2026-08-02 PR-XX merge-asset-integration):
    # 從 input_dir 內 XX_extracted/ 的 lang 檔 key-by-key 合併進
    # output_dir/lang_output/assets/{modid}/lang/{xx_yy}.json
    # config flag "enable_extracted_to_assets_merge" 控制是否跑。
    _run_extracted_stage2(
        folder_errors,
        output_dir,
        session,
        progress_start,
        progress_end,
        {
            k: options[k]
            for k in ("use_translation_db", "translation_db_version")
            if k in options
        },
    )


def _new_folder_stats() -> dict:
    """資料夾合併的統計計數器初始值。"""
    return {
        "total_folders": 1,
        "success_folders": 0,
        "failed_folders": 0,
        "errored_files": 0,
        "failed_folders_list": [],
    }


def run_merge_folder_batch_service(
    input_dir: str,
    output_dir: str,
    session,
    only_process_lang,
    process_zh_cn: bool | None = None,
    patchouli_skip: bool | None = None,
    patchouli_threshold: float | None = None,
    zh_en_threshold: int | None = None,
    progress_start: float = 0.0,
    progress_end: float = 1.0,
    finish_session: bool = True,
    skip_missing_input: bool = False,
    use_translation_db: bool | None = None,
    translation_db_version: str | None = None,
):
    """以資料夾為單位進行合併（支援 generator merge）。

    與 run_merge_zip_batch_service 結構相同，但使用 merge_zhcn_to_zhtw_from_folder。

    輸入資料夾不存在時預設視為失敗（階段 1 失敗、略過階段 2、任務標為 ERROR），
    不會顯示「翻譯已完成」；路徑存在但不是資料夾也視為失敗。``skip_missing_input=True``
    給一鍵流程使用：提取沒有產生某類內容（例如沒有 Patchouli 書籍）時不會建立輸出資料夾，
    這時明確記錄「略過」並視為沒有工作可做，而不是失敗（只限路徑不存在；型別不對仍是失敗）。
    """
    ensure_pipeline_logging()
    UI_LOG_HANDLER.set_session(session)
    output_existed_before, lease = True, None

    stats = _new_folder_stats()
    folder_errors = []
    finished = False  # generator 被 close（取消）時，yield 之後的 finish 不會執行；finally 補上

    try:
        lease, output_existed_before = _claim_output(output_dir)  # 被占用 → 錯誤路徑
        _session_log(session, f"[資料夾] 開始處理：{os.path.basename(input_dir)}")

        # 只有「路徑不存在」才能略過；存在但型別不對（例如是檔案）一律交給核心判為錯誤
        skipped = skip_missing_input and not os.path.exists(input_dir)
        if skipped:
            _session_log(
                session,
                f"[資料夾] 略過：輸入資料夾不存在（本次提取沒有產生這類內容）：{input_dir}",
                "warning",
            )

        try:
            if not skipped:
                _run_folder_stages(
                    input_dir,
                    output_dir,
                    session,
                    only_process_lang,
                    {
                        "process_zh_cn": process_zh_cn,
                        "patchouli_skip": patchouli_skip,
                        "patchouli_threshold": patchouli_threshold,
                        "zh_en_threshold": zh_en_threshold,
                        "use_translation_db": use_translation_db,
                        "translation_db_version": translation_db_version,
                    },
                    progress_start,
                    progress_end,
                    folder_errors,
                )

        except Exception as e:  # noqa: BLE001
            tb = traceback.format_exc()
            _session_log(session, f"[資料夾] 錯誤：{input_dir}\n{e!r}\n{tb}", "error")
            folder_errors.append(str(e))

        _record_folder_result(stats, input_dir, folder_errors)

        update = _finish_folder_run(session, stats, output_dir, bool(folder_errors))
        yield update
        if finish_session:
            session.finish()  # 失敗時已 set_error()，finish 維持 ERROR 並離開 active
            finished = True

    except Exception as e:  # noqa: BLE001
        error_summary = _fatal_folder_error(session, stats, output_dir, e)
        if finish_session:
            session.finish()
            finished = True
        yield {"progress": 1.0, "log": None, "error": True, "summary": error_summary}

    finally:
        try:
            _release_merge_output(
                output_dir, output_existed_before, finished, session, lease
            )
        finally:
            UI_LOG_HANDLER.set_session(None)
            if finish_session and not finished:
                session.finish()  # 取消（generator.close）等沒走到 finish 的路徑
