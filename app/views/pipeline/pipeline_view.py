"""模組流水線翻譯打包視圖

用途：
- 提供模組流水線翻譯打包工作台 UI
- 包含：Mod 來源/輸出目錄選擇、按鈕、API 設定
- 各步驟對話框已拆分至 app/views/pipeline/ 目錄
"""

import os
from functools import partial

import flet as ft

from app.ui import kit
from app.ui.design import C
from app.ui.snack import show_snack
from app.views.pipeline.pipeline_actions import PipelineActions, session_failed
from app.views.pipeline.pipeline_bundle_dialog import open_bundle_dialog
from app.views.pipeline.pipeline_config import PipelineConfig, normalize_extract_mode
from app.views.pipeline.pipeline_extract_dialog import open_extract_dialog
from app.views.pipeline.pipeline_merge_dialog import open_merge_dialog
from app.views.pipeline.pipeline_one_click_dialog import open_one_click_dialog
from app.views.pipeline.pipeline_progress import PipelineProgressPanel
from app.views.pipeline.pipeline_session import PipelineRunner
from app.views.pipeline.pipeline_translate_dialog import open_translate_dialog
from app.views.pipeline.pipeline_widgets import PipelineWidgetsMixin
from translation_tool.utils.log_unit import log_info, log_warning


class PipelineView(PipelineWidgetsMixin, ft.Column):
    """模組流水線翻譯打包工作台視圖"""

    def __init__(
        self,
        page: ft.Page,
        file_picker: ft.FilePicker,
        *,
        actions: PipelineActions | None = None,
        session_factory=None,
        launch_worker=None,
    ):
        """``actions``／``session_factory``／``launch_worker`` 供測試注入（預設為真實實作）。"""
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

        self.actions = actions or PipelineActions()
        self.progress_panel = PipelineProgressPanel(page, on_cancel=self._on_cancel)
        self.runner = PipelineRunner(
            page,
            self.progress_panel,
            self._update_progress,
            session_factory=session_factory,
            launch_worker=launch_worker,
            session_failed=session_failed,
        )

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
    # 步驟啟動（委派給 PipelineActions／PipelineRunner）
    # =============================================================================

    def _start_single_step(self, step_num: int, name: str, service_fn):
        """單一步驟按鈕：顯示進度面板、停用按鈕，並交給 runner 在背景執行。"""
        self._show_progress_panel()
        self._begin_run()
        self.runner.start_single(step_num, name, service_fn, self._end_run)

    def _run_extraction(
        self, mods_dir: str, output_dir: str, mode: str, lang_codes: list[str]
    ):
        """執行抽取資源（背景執行）。"""
        self._start_single_step(
            1,
            f"抽取資源（{mode}）",
            lambda session: self.actions.extract(
                session, mods_dir, output_dir, mode, lang_codes
            ),
        )

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
        """執行語系比對（背景執行）。"""
        self._start_single_step(
            2,
            "語系比對",
            lambda session: self.actions.merge(
                session,
                input_src,
                output_dir,
                input_mode,
                only_lang=only_lang,
                process_zh_cn=process_zh_cn,
                patchouli_skip=patchouli_skip,
                patchouli_threshold=patchouli_threshold,
                zh_en_threshold=zh_en_threshold,
            ),
        )

    def _run_translate(
        self, input_dir: str, output_dir: str, dry_run: bool, write_new_cache: bool
    ):
        """執行啟動翻譯（背景執行）。"""
        self._start_single_step(
            3,
            f"啟動翻譯（{'Dry-Run' if dry_run else '正式'}）",
            lambda session: self.actions.translate(
                session,
                input_dir,
                output_dir,
                dry_run=dry_run,
                write_new_cache=write_new_cache,
            ),
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
        """執行打包資源（背景執行）。"""
        self._start_single_step(
            4,
            f"打包資源（{os.path.basename(output_zip_path)}）",
            lambda session: self.actions.bundle(
                session,
                input_root_dir=input_root_dir,
                output_zip_path=output_zip_path,
                description=description,
                min_format=min_format or 0,
                max_format=max_format or 0,
                pack_image_path=pack_image_path,
                extra_folders=extra_folders or None,
            ),
        )

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

        steps = self.actions.one_click_steps(
            config, cfg, mode, lang_codes, merge_options
        )
        self.runner.start_sequence(steps, self._end_run)

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

        mode = normalize_extract_mode(config.get("mode"))
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

    def _set_buttons_disabled(self, disabled: bool):
        for btn in self._run_buttons:
            btn.disabled = disabled
        if self._one_click_button is not None:
            self._one_click_button.disabled = disabled

    def _begin_run(self):
        """開始執行：重設取消狀態、停用按鈕、顯示取消按鈕（UI 執行緒）。"""
        self.runner.reset_cancel()
        self._set_buttons_disabled(True)
        self.progress_panel.set_running(True)
        self._page.update()

    def _end_run(self):
        self.progress_panel.set_running(False)
        self._reenable_buttons()

    def _on_cancel(self):
        """要求取消：目前步驟在下一個檢查點停止，其餘步驟不再執行。"""
        if not self.runner.request_cancel():
            return
        self.progress_panel.cancel_button.disabled = True
        self.progress_panel.add_log("⏹ 正在取消…（等待目前的檢查點）", "warning")
        log_warning(
            "⏹ 正在取消…（等待目前的檢查點）",
            extra={"ui_mirror": True},
        )
        self._page.update()

    def _reenable_buttons(self, e=None):
        self._set_buttons_disabled(False)
        self._page.update()

    # =============================================================================
    # Lifecycle
    # =============================================================================

    def will_unmount(self):
        """換頁／關閉：停止日誌輪詢（idempotent）；背景步驟照常執行。"""
        self.runner.on_unmount()

    def did_mount(self):
        """重新掛載：步驟仍在追蹤就接續輪詢。"""
        self.runner.on_mount()
