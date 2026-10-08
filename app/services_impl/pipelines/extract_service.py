"""Extract pipeline service wrappers.

PR19：將 extract 類 service 從 app.services.py 抽離到 pipelines 子模組，
由 app.services 持續做 façade / re-export，維持 UI import 相容。

方案 2（廢除 poller）：worker 直接更新 UI，與 BundlerView 架構一致。

本模組職責：
- 統一管理 Lang/Book/Dual 三種提取模式的 Generator 呼叫
- 提供路徑準備工具（prepare_extraction_paths）以取代 UI 層硬編碼的拼接邏輯
- 透過 TaskSession 統一管理任務狀態、日誌、進度
"""

import logging
import ntpath
import os
import posixpath
import subprocess
import sys
import traceback
from typing import Any

from app.services_impl.logging_service import (
    GLOBAL_LOG_LIMITER,
    UI_LOG_HANDLER,
)
from app.services_impl.pipelines._pipeline_logging import (
    ensure_pipeline_logging,
    mirror_session_log,
)
from app.tasks.task_session import TaskSession
from translation_tool.core.jar_processor import (
    extract_book_files_generator,
    extract_dual_files_generator,
    extract_lang_files_generator,
    find_jar_files,  # noqa: F401 - 轉出給 View（#136）
    preview_extraction_generator,  # noqa: F401 - 轉出給 View（#136）
)
from translation_tool.utils.cancellation import (
    TaskCancelled,
    cancel_scope,
    is_cancelled,
)
from translation_tool.utils.config_manager import (
    load_config,
    validate_output_folder_suffix,
)

logger = logging.getLogger(__name__)


def _session_log(session, text: str, level: str = "info") -> None:
    mirror_session_log(session, logger, text, level)


# View 經由本模組取用引擎的提取／預覽 generator（#136）：上方 import 即為對外介面


def _select_extraction_generator(
    mode: str, mods_dir: str, output_dir: str, lang_codes=None, skip_zh_cn=False
):
    """根據 mode 選擇對應的提取 Generator。

    Args:
        mode: 提取模式（'lang' / 'book' / 'dual'）
        mods_dir: Mod 目錄路徑
        output_dir: 輸出目錄路徑
        lang_codes: 指定要提取的語言代碼列表（lang/book 模式使用）
        skip_zh_cn: 是否跳過 zh_cn（dual 模式使用）

    Returns:
        對應模式的 Generator 物件
    """
    if mode == "lang":
        return extract_lang_files_generator(mods_dir, output_dir, lang_codes=lang_codes)
    if mode == "book":
        return extract_book_files_generator(mods_dir, output_dir)
    # dual 模式使用 skip_zh_cn 參數
    return extract_dual_files_generator(mods_dir, output_dir, skip_zh_cn=skip_zh_cn)


def _prepare_output_path(
    mods_dir: str,
    mode: str,
    *,
    preview: bool,
    output_path: str = "",
) -> str:
    """解析輸出路徑；空白路徑一律在來源資料夾同層建立命名目錄。"""
    suffix_key = {
        (False, "lang"): "lang_extract",
        (False, "book"): "book_extract",
        (False, "dual"): "dual_extract",
        (True, "lang"): "lang_preview",
        (True, "book"): "book_preview",
        (True, "dual"): "dual_preview",
    }.get((preview, mode))
    if suffix_key is None:
        raise ValueError(f"Invalid extraction mode: {mode!r}")

    if output_path and output_path.strip():
        return output_path

    source = os.fspath(mods_dir or "").strip()
    if not source:
        return ""

    suffix = get_output_folder_names()[suffix_key]
    validate_output_folder_suffix(suffix, suffix_key)

    # `os.path` is ntpath on Windows. Also recognize Windows absolute paths in
    # cross-platform tests so drive/root semantics are not interpreted as POSIX.
    if ntpath.splitdrive(source)[0] or ("\\" in source and "/" not in source):
        path_module = ntpath
    elif source.startswith("/") and "\\" not in source:
        path_module = posixpath
    else:
        path_module = os.path
    normalized_source = path_module.normpath(source)
    source_name = path_module.basename(normalized_source)
    if not source_name:
        raise ValueError(f"Cannot derive output path from source: {mods_dir!r}")

    # Avoid repeating a suffix when an already-generated output folder is used
    # as the source for another extraction.
    if source_name.endswith(suffix):
        return normalized_source

    return path_module.join(
        path_module.dirname(normalized_source), source_name + suffix
    )


def prepare_extraction_paths(mods_dir: str, mode: str, output_path: str = "") -> str:
    """統一處理提取輸出路徑。

    取代原本散落在 extractor_dialog.py 中的路徑拼接邏輯。
    UI 層不應再自行讀取 config 與拼接子資料夾。

    Args:
        mods_dir: Mod 來源資料夾路徑
        mode: 提取模式（'lang' / 'book' / 'dual'）
        output_path: 外部指定的輸出路徑（可為空；有值時直接使用，不再加子資料夾）

    Returns:
        最終輸出路徑
    """
    return _prepare_output_path(mods_dir, mode, preview=False, output_path=output_path)


def get_output_folder_names() -> dict[str, str]:
    """取得 config 中的 output_folder_names 設定（含預設值）。

    取代 View 層內直接呼叫 load_config() + folder_names.get() 的樣板程式碼。
    讓 View 層只需呼叫此函數即可取得所有命名規則。

    Returns:
        dict 包含以下 key：
        - lang_extract / book_extract / dual_extract（提取模式的輸出後綴）
        - lang_preview / book_preview / dual_preview（預覽模式的輸出後綴）
    """
    cfg = load_config()
    folder_names = cfg.get("extractor", {}).get("output_folder_names", {})
    return {
        "lang_extract": folder_names.get("lang_extract", "_提取lang_輸出"),
        "book_extract": folder_names.get("book_extract", "_提取book_輸出"),
        "dual_extract": folder_names.get("dual_extract", "_提取both_輸出"),
        "lang_preview": folder_names.get("lang_preview", "_預覽lang_輸出"),
        "book_preview": folder_names.get("book_preview", "_預覽book_輸出"),
        "dual_preview": folder_names.get("dual_preview", "_預覽both_輸出"),
    }


def get_target_language() -> str:
    """讀取歷史目標語系設定，僅保留相容性。

    `extractor.target_language` 沒有正式的 production caller；新的提取流程
    使用語系清單與單次 skip 開關，不應在這裡自行推導新的業務規則。

    取代 View 層內直接呼叫 load_config() 的反模式。

    Returns:
        目標語系代碼，預設為 "zh_tw"
    """
    cfg = load_config()
    return cfg.get("extractor", {}).get("target_language", "zh_tw")


def get_lang_codes(*, skip_zh_cn: bool | None = None) -> list[str]:
    """從 config 讀取 JAR 提取的預設語系代碼列表。

    取代 View/Dialog 內直接呼叫 load_config() + get("jar_extractor") 的反模式。

    Returns:
        語系代碼列表，預設為 ["en_us", "zh_cn", "zh_tw"]
    """
    cfg = load_config()
    if skip_zh_cn is None:
        skip_zh_cn = bool(cfg.get("extractor", {}).get("skip_zh_cn_extract", False))
    codes = cfg.get("jar_extractor", {}).get("lang_codes", ["en_us", "zh_cn", "zh_tw"])
    if not isinstance(codes, list) or not codes:
        codes = ["en_us", "zh_cn", "zh_tw"]
    if skip_zh_cn:
        codes = [code for code in codes if code != "zh_cn"]
    return codes


def get_skip_zh_cn_extract() -> bool:
    """取得提取頁單次操作開關的預設值。"""
    cfg = load_config()
    return bool(cfg.get("extractor", {}).get("skip_zh_cn_extract", False))


def prepare_preview_paths(mods_dir: str, mode: str, output_path: str = "") -> str:
    """根據模式產生預覽後確認提取時使用的輸出路徑。

    與 prepare_extraction_paths 對稱，但用於 preview 模式。
    取代 extractor_dialog.py 與 extractor_actions.py 中重複的「
    lang_preview / book_preview 拼接邏輯。

    Args:
        mods_dir: Mod 來源資料夾路徑
        mode: 預覽模式（'lang' / 'book' / 'dual'）
        output_path: 明確指定的輸出位置；非空時原樣沿用

    Returns:
        確認執行後的輸出路徑（預設位於 mods_dir 同層，加上 preview 後綴）。
        此函式只解析路徑，不建立輸出目錄；空白來源回傳空字串。
    """
    return _prepare_output_path(mods_dir, mode, preview=True, output_path=output_path)


def _end_failed(session: TaskSession, finish_session: bool) -> None:
    """失敗收尾：一律 ``set_error()`` → ``finish()``（由本 service 擁有 session 生命週期時）。

    ``set_error()`` 在 ``TaskManager`` 不是 terminal event，必須再 ``finish()`` 才會離開 active。
    """
    session.set_error()
    if finish_session:
        session.finish()


def _run_extraction_with_session(
    generator,
    session: TaskSession,
    mode_label: str,
    *,
    finish_session: bool = True,
    progress_start: float = 0.0,
    progress_end: float = 1.0,
) -> None:
    """統一的 Generator 處理邏輯。

    三種提取模式（lang / book / dual）共用此流程：
    1. 透過 GLOBAL_LOG_LIMITER 過濾高頻日誌
    2. 寫入 TaskSession（log / progress / error）
    3. 完成或錯誤時呼叫 session.finish() / session.set_error()

    Args:
        generator: 對應模式的 Generator
        session: 任務 Session
        mode_label: 模式標籤，用於錯誤訊息（'Lang' / 'Book' / 'Dual'）
    """
    # error / stats 一律從原始 update 讀取：filter 只負責 UI 日誌節流，
    # 生命週期判斷不依賴它的回傳值。
    failures = _FailureTracker()

    def cancellable_updates():
        try:
            with cancel_scope(lambda: session.cancel_requested):
                yield from generator
        except TaskCancelled:
            yield {"cancelled": True}

    for update in cancellable_updates():
        if update.get("cancelled"):
            _session_log(session, f"⏹ {mode_label} 提取已取消", level="warning")
            if finish_session:
                session.finish()
            return
        if is_cancelled():
            # 在 JAR 之間停止（一鍵流水線的取消）
            _session_log(session, f"⏹ {mode_label} 提取已取消", level="warning")
            if finish_session:
                session.finish()
            return
        failures.observe(update)
        filtered: dict[str, Any] | None = GLOBAL_LOG_LIMITER.filter(update)
        if filtered is not None:
            if "log" in filtered:
                session.add_log(filtered["log"])
            if "progress" in filtered:
                phase_progress = max(0.0, min(1.0, float(filtered["progress"])))
                session.set_progress(
                    progress_start + (progress_end - progress_start) * phase_progress
                )

        if update.get("error"):
            _flush_limiter_to_session(session)
            _end_failed(session, finish_session)
            return

    _flush_limiter_to_session(session)

    # 有任何無法處理的 JAR：提取結果不完整，步驟不可算成功
    # （一鍵流水線會因此停止，不會以不完整的提取結果繼續合併 / 翻譯 / 打包）
    total_failures = failures.total()
    if total_failures > 0:
        if failures.last_stats is not None:
            session.set_summary(dict(failures.last_stats, failures=total_failures))
        _session_log(
            session,
            f"❌ {mode_label} 提取有 {total_failures} 個 JAR 無法處理（檔案可能已損毀），"
            "已提取的檔案保留，但此步驟視為失敗",
            level="error",
        )
        _end_failed(session, finish_session)
        return
    if finish_session:
        session.finish()


def _flush_limiter_to_session(session: TaskSession) -> None:
    final: dict[str, Any] | None = GLOBAL_LOG_LIMITER.flush()
    if final and "log" in final:
        session.add_log(final["log"])


class _FailureTracker:
    """從提取 generator 的 stats 累計無法處理的 JAR 數。

    - 單一模式（lang / book）：最終 stats（無 phase）即為總數
    - dual：各 phase 的 stats 分別記錄；若有合計（無 phase 或 phase 非 lang/book）
      以合計為準，否則加總各 phase，避免只看最後一個 phase 而漏掉失敗
    """

    def __init__(self) -> None:
        self.by_phase: dict[str, int] = {}
        self.combined: int | None = None
        self.last_stats: dict[str, Any] | None = None

    def observe(self, update: dict[str, Any]) -> None:
        stats = update.get("stats")
        if not isinstance(stats, dict):
            return
        self.last_stats = stats
        try:
            count = int(stats.get("failures", 0) or 0)
        except (TypeError, ValueError):
            count = 0
        phase = update.get("phase")
        if phase in ("lang", "book"):
            self.by_phase[phase] = count
        else:
            self.combined = count

    def total(self) -> int:
        phase_sum = sum(self.by_phase.values())
        if self.combined is None:
            return phase_sum
        return max(self.combined, phase_sum)


def run_extraction_loop(
    generator,
    cancelled_flag=None,
    on_update=None,
):
    """從 Generator 中提取更新並調用回調函數（適用於 Dialog 等不需 TaskSession 的場景）。

    此函數供 extractor_dialog.py 等 UI 層使用，讓 Dialog 仍保有
    「直接處理 Generator yield + 立即更新 UI」的彈性，但不必重複
    Generator 過濾與 cancelled 檢查的樣板程式碼。

    Args:
        generator: 對應模式的 Generator（lang/book/dual）
        cancelled_flag: 長度為 1 的 list，用於執行緒間通訊取消（[False]）
                       None 表示不可取消
        on_update: 收到 update 時的回調函數 (update_dict) -> None

    Returns:
        統計 dict，包含 success / warnings / failures
        Phase 3 (2026-07-13): DUAL mode 也會拆出 lang / book sub-dict,
        給 extractor_dialog.update_stats 顯示 LANG/BOOK 分區用。
    """
    stats = {
        "success": 0,
        "warnings": 0,
        "failures": 0,
        "lang": {"success": 0, "warnings": 0, "failures": 0},
        "book": {"success": 0, "warnings": 0, "failures": 0},
    }

    def cancellable_updates():
        def check() -> bool:
            return bool(cancelled_flag is not None and cancelled_flag[0])

        with cancel_scope(check):
            try:
                yield from generator
            except TaskCancelled:
                return

    updates = cancellable_updates()
    for update in updates:
        if cancelled_flag is not None and cancelled_flag[0]:
            updates.close()
            return stats

        if "stats" in update:
            result = update["stats"]
            stats["success"] = result.get("success", 0)
            stats["warnings"] = result.get("warnings", 0)
            stats["failures"] = result.get("failures", 0)
            # Phase 3: DUAL mode 時,generator yield {"phase": "lang"/"book", "stats": {...}},
            # 把 phase stats 拆出來,讓 update_stats 顯示 LANG/BOOK 分區。
            phase = update.get("phase")
            if phase in ("lang", "book"):
                # 即使 sub-dict 已 init (line 227),仍要 dict() 拷貝,避免外部改 result 影響 stats
                stats[phase] = dict(result)

        if update.get("error"):
            stats["failures"] += 1

        if on_update is not None:
            on_update(update)

    return stats


def run_lang_extraction_service(
    mods_dir: str,
    output_dir: str,
    session: TaskSession,
    lang_codes: list[str] | None = None,
    *,
    manage_session: bool = True,
    progress_start: float = 0.0,
    progress_end: float = 1.0,
) -> None:
    """執行語言檔擷取服務。

    Args:
        mods_dir: Mod 目錄路徑
        output_dir: 輸出目錄路徑
        session: 任務 Session
        lang_codes: 指定要提取的語言代碼列表，若為 None 則從 config 讀取
    """
    ensure_pipeline_logging()
    try:
        if manage_session:
            session.start()
        UI_LOG_HANDLER.set_session(session)
        generator = _select_extraction_generator(
            "lang", mods_dir, output_dir, lang_codes
        )
        _run_extraction_with_session(
            generator,
            session,
            "Lang",
            finish_session=manage_session,
            progress_start=progress_start,
            progress_end=progress_end,
        )
    except Exception as e:  # noqa: BLE001
        full_traceback = traceback.format_exc()
        # 畫面與後台各一份：只經 _session_log 寫入（它同時寫 session 與後台），不要再另外 logger.error
        _session_log(
            session, f"[致命錯誤] Lang 檔案提取失敗：{e!r}\n{full_traceback}", "error"
        )
        _end_failed(session, manage_session)
        GLOBAL_LOG_LIMITER.flush()
    finally:
        # ⭐ 避免 handler 留著舊 session
        UI_LOG_HANDLER.set_session(None)


def run_book_extraction_service(
    mods_dir: str,
    output_dir: str,
    session: TaskSession,
    lang_codes: list[str] | None = None,
    *,
    manage_session: bool = True,
    progress_start: float = 0.0,
    progress_end: float = 1.0,
) -> None:
    """執行書本檔擷取服務。

    Args:
        mods_dir: Mod 目錄路徑
        output_dir: 輸出目錄路徑
        session: 任務 Session
        lang_codes: 指定要提取的語言代碼列表，若為 None 則從 config 讀取
    """
    ensure_pipeline_logging()
    try:
        if manage_session:
            session.start()
        UI_LOG_HANDLER.set_session(session)
        generator = _select_extraction_generator(
            "book", mods_dir, output_dir, lang_codes
        )
        _run_extraction_with_session(
            generator,
            session,
            "Book",
            finish_session=manage_session,
            progress_start=progress_start,
            progress_end=progress_end,
        )
    except Exception as e:  # noqa: BLE001
        full_traceback = traceback.format_exc()
        _session_log(
            session, f"[致命錯誤] Book 檔案提取失敗：{e!r}\n{full_traceback}", "error"
        )
        _end_failed(session, manage_session)
        GLOBAL_LOG_LIMITER.flush()
    finally:
        # ⭐ 避免 handler 留著舊 session
        UI_LOG_HANDLER.set_session(None)


def run_dual_extraction_service(
    mods_dir: str,
    output_dir: str,
    session: TaskSession,
    lang_codes: list[str] | None = None,
) -> None:
    """執行 Dual（Lang + Book）雙模式提取服務。

    Args:
        mods_dir: Mod 目錄路徑
        output_dir: 輸出目錄路徑
        session: 任務 Session
        lang_codes: 指定要提取的語言代碼列表，若為 None 則從 config 讀取
    """
    ensure_pipeline_logging()
    try:
        session.start()
        UI_LOG_HANDLER.set_session(session)
        generator = _select_extraction_generator(
            "dual", mods_dir, output_dir, lang_codes
        )
        _run_extraction_with_session(generator, session, "Dual")
    except Exception as e:  # noqa: BLE001
        full_traceback = traceback.format_exc()
        _session_log(
            session, f"[致命錯誤] Dual 提取失敗：{e!r}\n{full_traceback}", "error"
        )
        _end_failed(session, True)
        GLOBAL_LOG_LIMITER.flush()
    finally:
        # ⭐ 避免 handler 留著舊 session
        UI_LOG_HANDLER.set_session(None)


def open_output_folder(path: str) -> bool:
    """用系統預設檔案管理員開啟資料夾（**不等待**外部程式，可在 UI handler 內直接呼叫）。

    取代 UI 層直接呼叫 os.startfile 的反模式；UI 層不應處理 OS 層級的操作。

    - Windows：``os.startfile``（只交給 shell，不等待）。
    - macOS／Linux：``subprocess.Popen``（不 ``wait``）。原本的 ``subprocess.run(check=True)``
      會讓 Flet event loop 等到外部程式結束，違反 #114「sync handler 不得阻塞」。

    Args:
        path: 資料夾路徑

    Returns:
        True 表示已成功啟動；False 表示路徑不存在或無法啟動（錯誤只記錄，不會拋出）。
        啟動後外部程式自己失敗（例如非零 exit code）不會被偵測到。
    """
    if not path or not os.path.isdir(path):
        return False

    try:
        if os.name == "nt":  # Windows
            os.startfile(path)
        else:
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen(
                [opener, path],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        return True
    except Exception:
        logger.warning("開啟資料夾失敗：%s", path, exc_info=True)
        return False
