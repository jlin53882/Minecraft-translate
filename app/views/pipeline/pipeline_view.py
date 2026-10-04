"""模組流水線翻譯打包視圖

用途：
- 提供模組流水線翻譯打包工作台 UI
- 包含：Mod 來源/輸出目錄選擇、按鈕、API 設定
- 各步驟對話框已拆分至 app/views/pipeline/ 目錄
"""

import asyncio
import concurrent.futures
import inspect
import os
import threading
import traceback
from functools import partial

import flet as ft

from app.services_impl.pipelines.bundle_service import (
    build_bundle_staging,
    run_bundling_service,
)
from app.services_impl.pipelines.extract_service import (
    run_book_extraction_service,
    run_lang_extraction_service,
)
from app.services_impl.pipelines.lm_service import run_lm_translation_service
from app.services_impl.pipelines.merge_service import (
    run_merge_folder_batch_service,
    run_merge_zip_batch_service,
)
from app.tasks.task_session import TaskSession, tag_session
from app.ui import kit
from app.ui.design import C
from app.ui.snack import show_snack
from app.views.pipeline.pipeline_bundle_dialog import open_bundle_dialog
from app.views.pipeline.pipeline_config import PipelineConfig, _has_files
from app.views.pipeline.pipeline_extract_dialog import open_extract_dialog
from app.views.pipeline.pipeline_merge_dialog import open_merge_dialog
from app.views.pipeline.pipeline_one_click_dialog import open_one_click_dialog
from app.views.pipeline.pipeline_progress import (
    PipelineProgressPanel,
)
from app.views.pipeline.pipeline_translate_dialog import open_translate_dialog
from app.views.pipeline.pipeline_widgets import PipelineWidgetsMixin
from translation_tool.utils.cancellation import TaskCancelled, cancel_scope
from translation_tool.utils.log_unit import log_error, log_info, log_warning

# =============================================================================
# PipelineStepChip - 步驟狀態晶片
# =============================================================================


# =============================================================================
# PipelineProgressPanel - 日誌+進度面板
# =============================================================================


# =============================================================================
# PipelineView - 流水線主視圖
# =============================================================================


class PipelineView(PipelineWidgetsMixin, ft.Column):
    """模組流水線翻譯打包工作台視圖"""

    def __init__(self, page: ft.Page, file_picker: ft.FilePicker):
        super().__init__(expand=True, spacing=15)
        self._page = page
        self.file_picker = file_picker
        self.registry = None  # 預留給外部注入

        self.input_path_text = kit.text_field(
            hint="尚未選擇讀取來源...", mono=True, expand=True
        )
        self.output_path_text = kit.text_field(
            hint="尚未選擇輸出目的地...", mono=True, expand=True
        )
        self.progress_bar = kit.progress_bar(0, "em", height=8)
        self.progress_status = ft.Text("等待任務啟動...", size=12, color=C.MUTED)
        self.keys_container = ft.Column(spacing=10)
        self._run_buttons: list[ft.Button] = []

        self._cancel_event = threading.Event()
        self._current_session: TaskSession | None = None
        self.progress_panel = PipelineProgressPanel(page, on_cancel=self._on_cancel)

        self._lang_code_checks = {}
        self._one_click_button = None

        self._build_ui()

    def set_view_registry(self, registry):
        """將全域視圖註冊表注入視圖內。"""
        self.registry = registry
        self._page.update()

    def set_registry(self, registry):
        """將全域視圖註冊表注入視圖內（相容舊代碼）。"""
        self.set_view_registry(registry)

    def _update_progress(self, val, text):
        self.progress_bar.value = val
        self.progress_status.value = text
        self._page.update()

    async def _pick_input_dir(self, e=None):
        result = await self.file_picker.get_directory_path()
        if result:
            self.input_path_text.value = result
            self._page.update()

    async def _pick_output_dir(self, e=None):
        result = await self.file_picker.get_directory_path()
        if result:
            self.output_path_text.value = result
            self._page.update()

    def _show_progress_panel(self):
        """顯示進度面板並清除舊日誌"""
        self.progress_panel.clear_logs()
        self.progress_panel.start()
        self._page.update()

    # =============================================================================
    # Background Workers
    # =============================================================================

    # 輪詢間隔：背景任務的日誌/進度以這個頻率批次推到畫面
    _POLL_INTERVAL_SEC = 0.2

    def _ui(self, fn, *args, **kwargs):
        """在 Flet event loop 上執行 UI 更新（背景執行緒不可直接呼叫 page.update）。"""

        async def _run():
            fn(*args, **kwargs)

        return self._page.run_task(_run)

    async def _watch_session(self, session: TaskSession, done: threading.Event):
        """在 event loop 上輪詢 session，依 seq 只取新日誌並批次刷新畫面。"""
        last_seq = -1
        while True:
            finished = done.is_set()
            snap = session.snapshot()
            logs = snap.get("logs", [])
            if logs and logs[-1].seq < last_seq:  # session.start() 會重置 seq
                last_seq = -1
            new_items = [(e.text, e.level) for e in logs if e.seq > last_seq]
            if logs:
                last_seq = logs[-1].seq
            if new_items:
                self.progress_panel.log_view.add_many(
                    [(f">> {text}", level) for text, level in new_items]
                )
            progress = float(snap.get("progress", 0) or 0)
            self._update_progress(progress, f"{int(progress * 100)}%")
            if finished:
                return
            await asyncio.sleep(self._POLL_INTERVAL_SEC)

    @staticmethod
    def _session_failed(session: TaskSession) -> bool:
        """任務失敗：session 標記錯誤，或摘要中有失敗項目。"""
        if session.error:
            return True
        summary = session.snapshot().get("summary") or {}
        return any(
            summary.get(key, 0)
            for key in ("failed_zips", "failed_folders", "errored_files")
        )

    def _run_session_step(self, step_num: int, name: str, service_fn) -> bool:
        """在目前（背景）執行緒執行一個步驟，回傳是否成功。

        每個步驟使用獨立 TaskSession；service 回傳 generator 時會完整迭代
        （merge / bundle service 都是 generator，未迭代就不會執行）。
        """
        if self._cancel_event.is_set():
            return False
        session = tag_session(TaskSession(), "一鍵流水線", "pipeline")
        self._current_session = session
        done = threading.Event()
        self._ui(self.progress_panel.set_step_running, step_num, name)
        self._ui(self.progress_panel.add_log, f"▶ 開始：{name}")
        watcher = self._page.run_task(self._watch_session, session, done)
        try:
            # 取消檢查：翻譯在批次之間 / 等待限流時、提取在 JAR 之間停止
            with cancel_scope(self._cancel_event.is_set):
                result = service_fn(session)
                if inspect.isgenerator(result):
                    for _ in result:
                        if self._cancel_event.is_set():
                            result.close()
                            break
        except TaskCancelled:
            pass
        except Exception as ex:  # noqa: BLE001 - 背景步驟邊界：任何錯誤都轉成步驟失敗
            log_error(f"[Pipeline] {name} 失敗：{ex}\n{traceback.format_exc()}")
            session.add_log(f"❌ 錯誤：{ex}", level="error")
            session.set_error()
        finally:
            self._current_session = None
            if self._cancel_event.is_set():
                session.add_log(f"⏹ {name} 已取消", level="warning")
            done.set()
        try:
            if watcher is not None:
                watcher.result(timeout=30)
        except (
            concurrent.futures.TimeoutError,
            concurrent.futures.CancelledError,
        ) as ex:
            log_warning(f"[Pipeline] {name} 日誌同步未完成：{ex!r}")

        cancelled = self._cancel_event.is_set()
        ok = not cancelled and not self._session_failed(session)

        def _finish():
            self.progress_panel.finish_step(step_num, ok, cancelled=cancelled)
            if cancelled:
                self.progress_panel.add_log(f"⏹ {name} 已取消", "warning")
                self._update_progress(1.0, "已取消")
                return
            self.progress_panel.add_log(f"✅ {name} 完成" if ok else f"❌ {name} 失敗")
            self._update_progress(1.0, "完成" if ok else "失敗")

        self._ui(_finish)
        return ok

    def _start_single_step(self, step_num: int, name: str, service_fn):
        """單一步驟按鈕：顯示進度面板、停用按鈕，並在背景執行。"""
        self._show_progress_panel()
        self._begin_run()

        def worker():
            try:
                self._run_session_step(step_num, name, service_fn)
            finally:
                self._ui(self._end_run)

        threading.Thread(target=worker, daemon=True).start()

    def _run_extraction(
        self, mods_dir: str, output_dir: str, mode: str, lang_codes: list[str]
    ):
        """執行抽取資源（背景執行緒）。"""
        cfg = PipelineConfig(mods_dir, output_dir)

        def service(session):
            if mode in ("lang", "dual"):
                os.makedirs(cfg.extract_lang_output_dir, exist_ok=True)
                run_lang_extraction_service(
                    mods_dir,
                    cfg.extract_lang_output_dir,
                    session,
                    lang_codes=lang_codes,
                )
                if session.error:
                    return
            if mode in ("book", "dual"):
                os.makedirs(cfg.extract_book_output_dir, exist_ok=True)
                run_book_extraction_service(
                    mods_dir,
                    cfg.extract_book_output_dir,
                    session,
                    lang_codes=lang_codes,
                )

        self._start_single_step(1, f"抽取資源（{mode}）", service)

    def _run_merge(
        self,
        input_src,
        output_dir: str,
        input_mode: str,
        only_lang: bool,
        process_zh_cn: bool,
        patchouli_skip: bool,
        patchouli_threshold: float,
        zh_en_threshold: int,
        lang_codes: list[str],
    ):
        """執行語系比對（背景執行緒）。"""
        options = {
            "output_dir": output_dir,
            "only_process_lang": only_lang,
            "process_zh_cn": process_zh_cn,
            "patchouli_skip": patchouli_skip,
            "patchouli_threshold": patchouli_threshold,
            "zh_en_threshold": zh_en_threshold,
        }

        def service(session):
            os.makedirs(output_dir, exist_ok=True)
            if input_mode == "folder":
                return run_merge_folder_batch_service(
                    input_dir=input_src, session=session, **options
                )
            zip_paths = input_src if isinstance(input_src, list) else [input_src]
            return run_merge_zip_batch_service(
                zip_paths=zip_paths, session=session, **options
            )

        self._start_single_step(2, "語系比對", service)

    # =============================================================================
    # Button Handlers
    # =============================================================================

    def _on_extract_click(self, e=None):
        input_val = (self.input_path_text.value or "").strip()
        output_val = (self.output_path_text.value or "").strip()
        if not input_val:
            show_snack(self._page, "⚠️ 請填寫 Mod 來源路徑")
            return
        if not output_val:
            show_snack(self._page, "⚠️ 請填寫輸出目錄路徑")
            return
        open_extract_dialog(
            page=self._page,
            file_picker=self.file_picker,
            input_path=input_val,
            output_path=output_val,
            on_run_extraction=self._run_extraction,
            lang_code_checks=self._lang_code_checks,
            show_snack_bar=partial(show_snack, self._page),
        )

    def _on_merge_click(self, e=None):
        input_val = (self.input_path_text.value or "").strip()
        output_val = (self.output_path_text.value or "").strip()
        if not input_val:
            show_snack(self._page, "⚠️ 請填寫 Mod 來源路徑")
            return
        if not output_val:
            show_snack(self._page, "⚠️ 請填寫輸出目錄路徑")
            return
        open_merge_dialog(
            page=self._page,
            file_picker=self.file_picker,
            input_path=input_val,
            output_path=output_val,
            lang_code_checks=self._lang_code_checks,
            on_run_merge=self._run_merge,
            show_snack_bar=partial(show_snack, self._page),
        )

    def _on_translate_click(self, e=None):
        input_val = (self.input_path_text.value or "").strip()
        output_val = (self.output_path_text.value or "").strip()
        if not input_val:
            show_snack(self._page, "⚠️ 請填寫 Mod 來源路徑")
            return
        if not output_val:
            show_snack(self._page, "⚠️ 請填寫輸出目錄路徑")
            return
        open_translate_dialog(
            page=self._page,
            file_picker=self.file_picker,
            input_path=input_val,
            output_path=output_val,
            on_start_translate=self._run_translate,
            show_snack_bar=partial(show_snack, self._page),
        )

    def _on_bundle_click(self, e=None):
        input_val = (self.input_path_text.value or "").strip()
        output_val = (self.output_path_text.value or "").strip()
        if not input_val:
            show_snack(self._page, "⚠️ 請填寫 Mod 來源路徑")
            return
        if not output_val:
            show_snack(self._page, "⚠️ 請填寫輸出目錄路徑")
            return
        open_bundle_dialog(
            page=self._page,
            file_picker=self.file_picker,
            input_path=input_val,
            output_path=output_val,
            on_start_bundle=self._run_bundle,
            show_snack_bar=partial(show_snack, self._page),
        )

    def _on_one_click_click(self, e=None):
        input_val = (self.input_path_text.value or "").strip()
        output_val = (self.output_path_text.value or "").strip()
        log_info(f"Pipeline one_click: input=[{input_val}], output=[{output_val}]")
        if not input_val:
            show_snack(self._page, "⚠️ 請輸入 Mod 來源路徑")
            return
        if not output_val:
            show_snack(self._page, "⚠️ 請輸入輸出目錄路徑")
            return
        open_one_click_dialog(
            page=self._page,
            file_picker=self.file_picker,
            input_path=input_val,
            output_path=output_val,
            on_execute=self._on_one_click_execute,
            show_snack_bar=partial(show_snack, self._page),
        )

    def _on_one_click_execute(self, config: dict):
        prepared = self._prepare_one_click(config)
        if prepared is None:
            return
        cfg, mode, lang_codes, merge_options = prepared

        self._show_progress_panel()
        self._begin_run()

        steps = self._one_click_steps(config, cfg, mode, lang_codes, merge_options)
        self._start_one_click_worker(steps)

    def _prepare_one_click(self, config: dict):
        """檢查一鍵製作的輸入；通過時回傳 (cfg, mode, lang_codes, merge_options)，否則顯示提示並回傳 None。"""
        input_dir = (self.input_path_text.value or "").strip()
        output_dir = (self.output_path_text.value or "").strip()

        if not input_dir or not os.path.isdir(input_dir):
            show_snack(self._page, "❌ Mod 來源不存在或未選擇")
            return None
        if not output_dir or not os.path.isdir(output_dir):
            show_snack(self._page, "❌ 輸出目錄不存在或未選擇")
            return None

        mode = config.get("mode", "lang")
        lang_codes = config.get("lang_codes", [])
        if not lang_codes:
            show_snack(self._page, "⚠️ 請至少勾選一個語系代碼")
            return None

        cfg = PipelineConfig(input_dir, output_dir)
        merge_options = {
            "output_dir": cfg.merge_output_dir,
            "process_zh_cn": config.get("process_zh_cn", True),
            "patchouli_skip": config.get("patchouli_skip", False),
            "patchouli_threshold": config.get("patchouli_threshold", 0.5),
            "zh_en_threshold": config.get("zh_en_threshold", 2),
        }
        return cfg, mode, lang_codes, merge_options

    def _one_click_steps(self, config: dict, cfg, mode, lang_codes, merge_options):
        """一鍵製作的四個步驟：抽取、語系比對、翻譯、打包。"""

        def extract(session):
            if mode in ("lang", "dual"):
                os.makedirs(cfg.extract_lang_output_dir, exist_ok=True)
                run_lang_extraction_service(
                    cfg.input_dir,
                    cfg.extract_lang_output_dir,
                    session,
                    lang_codes=lang_codes,
                )
                if session.error:
                    return
            if mode in ("book", "dual"):
                os.makedirs(cfg.extract_book_output_dir, exist_ok=True)
                run_book_extraction_service(
                    cfg.input_dir,
                    cfg.extract_book_output_dir,
                    session,
                    lang_codes=lang_codes,
                )

        def merge(session):
            # 各抽取結果分別合併（lang 只處理語言檔，book 需處理 Patchouli 內容）
            os.makedirs(cfg.merge_output_dir, exist_ok=True)
            sources = []
            if mode in ("lang", "dual"):
                # 「只處理 lang 檔案」開關（一鍵對話框步驟 2）；book 來源固定要處理 Patchouli 內容
                sources.append(
                    (cfg.extract_lang_output_dir, config.get("only_lang", True))
                )
            if mode in ("book", "dual"):
                sources.append((cfg.extract_book_output_dir, False))
            for src, only_lang in sources:
                yield from run_merge_folder_batch_service(
                    input_dir=src,
                    session=session,
                    only_process_lang=only_lang,
                    **merge_options,
                )
                if self._session_failed(session):
                    return

        def translate(session):
            inputs = [d for d in cfg.translate_input_dirs if _has_files(d)]
            if not inputs:
                session.add_log("[系統] 沒有待翻譯內容，略過翻譯")
                return
            os.makedirs(cfg.translate_output_dir, exist_ok=True)
            for src in inputs:
                run_lm_translation_service(
                    input_dir=src,
                    output_dir=cfg.translate_output_dir,
                    session=session,
                    dry_run=config.get("dry_run", False),
                    export_lang=False,
                    write_new_cache=config.get("write_new_cache", True),
                )
                if session.error:
                    return

        def bundle(session):
            stats = build_bundle_staging(cfg.bundle_sources, cfg.bundle_staging_dir)
            session.add_log(
                f"[系統] 打包暫存完成：複製 {stats['copied']} 個、合併 {stats['merged']} 個檔案"
            )
            if not stats["copied"] and not stats["merged"]:
                session.add_log("❌ 沒有可打包的翻譯檔案", level="error")
                session.set_error()
                return
            yield from self._bundle_into_session(
                session,
                input_root_dir=cfg.bundle_staging_dir,
                output_zip_path=config.get("zip_output") or cfg.bundle_output_zip,
                description=config.get("description", ""),
                min_format=config.get("min_format") or 0,
                max_format=config.get("max_format") or 0,
                pack_image_path=config.get("pack_image"),
                extra_folders=config.get("extra_folders", []),
            )

        steps = [
            (1, "抽取資源", extract),
            (2, "語系比對", merge),
            (3, "啟動翻譯", translate),
            (4, "打包資源", bundle),
        ]
        return steps

    def _start_one_click_worker(self, steps) -> None:
        """在背景執行緒依序執行步驟，結束後恢復按鈕狀態。"""

        def worker():
            success = False
            try:
                for step_num, name, fn in steps:
                    if not self._run_session_step(step_num, name, fn):
                        return
                success = True
            except Exception as ex:  # noqa: BLE001 - 背景執行緒邊界，確保按鈕會恢復
                log_error(f"[Pipeline] 一鍵製作失敗：{ex}\n{traceback.format_exc()}")
                self._ui(self.progress_panel.add_log, f"❌ 流程失敗：{ex}", "error")
            finally:

                def _done():
                    cancelled = self._cancel_event.is_set()
                    self.progress_panel.finish_all(success, cancelled=cancelled)
                    if success:
                        self.progress_panel.add_log("✅ 一鍵製作完成！")
                    elif cancelled:
                        self.progress_panel.add_log("⏹ 一鍵製作已取消", "warning")
                    self._end_run()

                self._ui(_done)

        threading.Thread(target=worker, daemon=True).start()

    def _bundle_into_session(self, session: TaskSession, **kwargs):
        """執行打包 generator，並把日誌/進度/錯誤寫入 session。"""
        os.makedirs(os.path.dirname(kwargs["output_zip_path"]) or ".", exist_ok=True)
        for update_dict in run_bundling_service(**kwargs):
            if update_dict.get("log"):
                session.add_log(update_dict["log"])
            if update_dict.get("progress") is not None:
                session.set_progress(update_dict["progress"])
            if update_dict.get("error"):
                session.set_error()
                return
            yield update_dict

    def _set_buttons_disabled(self, disabled: bool):
        for btn in self._run_buttons:
            btn.disabled = disabled
        if self._one_click_button is not None:
            self._one_click_button.disabled = disabled

    def _begin_run(self):
        """開始執行：重設取消狀態、停用按鈕、顯示取消按鈕（UI 執行緒）。"""
        self._cancel_event.clear()
        self._set_buttons_disabled(True)
        self.progress_panel.set_running(True)
        self._page.update()

    def _end_run(self):
        self.progress_panel.set_running(False)
        self._reenable_buttons()

    def _on_cancel(self):
        """要求取消：目前步驟在下一個檢查點停止，其餘步驟不再執行。"""
        if self._cancel_event.is_set():
            return
        self._cancel_event.set()
        session = self._current_session
        if session is not None:
            session.request_cancel()
        self.progress_panel.cancel_button.disabled = True
        self.progress_panel.add_log("⏹ 正在取消…（等待目前的檢查點）", "warning")
        self._page.update()

    def _reenable_buttons(self, e=None):
        self._set_buttons_disabled(False)
        self._page.update()

    def _run_translate(
        self, input_dir: str, output_dir: str, dry_run: bool, write_new_cache: bool
    ):
        """執行啟動翻譯（背景執行緒）。"""

        def service(session):
            os.makedirs(output_dir, exist_ok=True)
            run_lm_translation_service(
                input_dir=input_dir,
                output_dir=output_dir,
                session=session,
                dry_run=dry_run,
                export_lang=False,
                write_new_cache=write_new_cache,
            )

        self._start_single_step(
            3, f"啟動翻譯（{'Dry-Run' if dry_run else '正式'}）", service
        )

    def _run_bundle(
        self,
        input_root_dir: str,
        output_zip_path: str,
        description: str,
        min_format,
        max_format,
        pack_image_path: str | None,
        extra_folders: list[str],
    ):
        """執行打包資源（背景執行緒）。"""

        def service(session):
            return self._bundle_into_session(
                session,
                input_root_dir=input_root_dir,
                output_zip_path=output_zip_path,
                description=description,
                min_format=min_format or 0,
                max_format=max_format or 0,
                pack_image_path=pack_image_path,
                extra_folders=extra_folders or None,
            )

        self._start_single_step(
            4, f"打包資源（{os.path.basename(output_zip_path)}）", service
        )
