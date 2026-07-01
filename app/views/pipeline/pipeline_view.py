"""模組流水線翻譯打包視圖

用途：
- 提供模組流水線翻譯打包工作台 UI
- 包含：Mod 來源/輸出目錄選擇、按鈕、API 設定
- 各步驟對話框已拆分至 app/views/pipeline/ 目錄
"""

import flet as ft
import threading
import time
import os

from app.ui.theme import (
    BLUE_600, BLUE_700, GREEN_700, TEAL_700, PURPLE_700,
    YELLOW_900, YELLOW, CYAN_400, CYAN_700, GREY_500, GREY_600,
    RED_400, ORANGE_700, WHITE, BLUE_50, GREY_200, BLUE_400,
    GREEN_600, GREEN_50, RED_50,
)
from app.logging.task_session import TaskSession
from translation_tool.utils.config_manager import load_config
from translation_tool.utils.log_unit import log_info
from app.services_impl.pipelines.extract_service import (
    run_lang_extraction_service,
    run_book_extraction_service,
)
from app.services_impl.pipelines.merge_service import run_merge_zip_batch_service, run_merge_folder_batch_service
from app.services_impl.pipelines.lm_service import run_lm_translation_service
from app.services_impl.pipelines.bundle_service import run_bundling_service
from app.views._log import LogView

from app.views.pipeline.pipeline_extract_dialog import open_extract_dialog
from app.views.pipeline.pipeline_merge_dialog import open_merge_dialog
from app.views.pipeline.pipeline_translate_dialog import open_translate_dialog
from app.views.pipeline.pipeline_bundle_dialog import open_bundle_dialog
from app.views.pipeline.pipeline_one_click_dialog import open_one_click_dialog


class PipelineConfig:
    """一鍵製作路徑設定檔"""

    def __init__(self, input_dir: str, output_dir: str):
        cfg = load_config()
        self.input_dir = input_dir
        self.output_dir = output_dir

        lang_merger = cfg.get("lang_merger", {})
        bundler = cfg.get("output_bundler", {})

        self.jar_mod_extract = "jar_mod_extract"
        self.lang_output_subfolder = "_提取lang_輸出"
        self.book_output_subfolder = "_提取book_輸出"

        self.locale_sort = "locale_sort"
        self.sort_output_subfolder = "_整理輸出"
        self.pending_folder = lang_merger.get("pending_folder_name", "待翻譯")
        self.organized_folder = lang_merger.get("pending_organized_folder_name", "待翻譯整理需翻譯")

        self.lm_translate = "lm_translate"
        self.translate_output_subfolder = "_翻譯輸出"

        self.output_zip_name = bundler.get("output_zip_name", "可使用翻譯.zip")

    @property
    def extract_lang_output_dir(self):
        return os.path.join(self.output_dir, self.jar_mod_extract, self.lang_output_subfolder)

    @property
    def extract_book_output_dir(self):
        return os.path.join(self.output_dir, self.jar_mod_extract, self.book_output_subfolder)

    @property
    def merge_input_dir(self):
        return os.path.join(self.output_dir, self.jar_mod_extract)

    @property
    def merge_output_dir(self):
        return os.path.join(self.output_dir, self.locale_sort, self.sort_output_subfolder)

    @property
    def translate_input_dir(self):
        return os.path.join(self.merge_output_dir, self.organized_folder)

    @property
    def translate_output_dir(self):
        return os.path.join(self.output_dir, self.lm_translate, self.translate_output_subfolder)

    @property
    def bundle_input_dir(self):
        return os.path.join(self.output_dir, self.lm_translate, self.translate_output_subfolder)

    @property
    def bundle_output_zip(self):
        return os.path.join(self.output_dir, self.output_zip_name)


# =============================================================================
# PipelineStepChip - 步驟狀態晶片
# =============================================================================

class PipelineStepChip:
    """單一步驟狀態晶片"""

    def __init__(self, name: str, step_num: int):
        self.name = name
        self.step_num = step_num
        self.status = "waiting"

        self.chip = ft.Chip(
            label=ft.Text(f"{step_num}. {name}"),
            bgcolor=GREY_200,
        )
        self.icon = ft.Icon(ft.Icons.CIRCLE, size=12, color=GREY_500)
        self.chip.leading = self.icon
        self._update_chip()

    def _update_chip(self):
        colors = {
            "waiting": (GREY_500, GREY_200),
            "running": (BLUE_400, BLUE_50),
            "done": (GREEN_600, GREEN_50),
            "failed": (RED_400, RED_50),
        }
        icons = {
            "waiting": ft.Icons.CIRCLE,
            "running": ft.Icons.PENDING,
            "done": ft.Icons.CHECK_CIRCLE,
            "failed": ft.Icons.ERROR,
        }
        color, bg = colors.get(self.status, (GREY_500, GREY_200))
        self.icon.name = icons[self.status]
        self.icon.color = color
        self.chip.bgcolor = bg

    def set_status(self, status: str):
        self.status = status
        self._update_chip()


# =============================================================================
# PipelineProgressPanel - 日誌+進度面板
# =============================================================================

class PipelineProgressPanel:
    """日誌+進度面板，顯示步驟狀態晶片、進度條、即時日誌"""

    def __init__(self, page: ft.Page):
        self._page = page
        self.steps = [
            PipelineStepChip("抽取資源", 1),
            PipelineStepChip("語系比對", 2),
            PipelineStepChip("啟動翻譯", 3),
            PipelineStepChip("打包資源", 4),
        ]
        self.current_step = None

        self.step_row = ft.Row(
            controls=[
                ft.Container(content=self.steps[0].chip, padding=5),
                ft.Icon(ft.Icons.ARROW_FORWARD, size=16, color=GREY_500),
                ft.Container(content=self.steps[1].chip, padding=5),
                ft.Icon(ft.Icons.ARROW_FORWARD, size=16, color=GREY_500),
                ft.Container(content=self.steps[2].chip, padding=5),
                ft.Icon(ft.Icons.ARROW_FORWARD, size=16, color=GREY_500),
                ft.Container(content=self.steps[3].chip, padding=5),
            ],
            spacing=5,
        )

        self.current_label = ft.Text("等待執行...", color=GREY_600, size=14)
        # PR refactor/unified-log-view: 改用 LogView widget
        # 統一深色容器 + 等寬字 + 等級顏色（從 theme）
        # 保留 height=120 限制
        self.log_view = LogView(
            page=page,
            mode="append",
            max_lines=500,
            height=120,
        )

        self.container = ft.Container(
            content=ft.Column([
                ft.Text("執行進度", weight="bold", color=BLUE_700),
                self.step_row,
                self.current_label,
                ft.Divider(),
                ft.Row([ft.Icon(ft.Icons.INFO, size=14, color=GREY_500),
                        ft.Text("步驟日誌：", size=12, color=GREY_600)]),
                self.log_view,
            ], spacing=5),
            padding=12,
            bgcolor=BLUE_50,
            border_radius=10,
            visible=False,
        )

    def start(self):
        self.container.visible = True
        for step in self.steps:
            step.set_status("waiting")

    def set_step_running(self, step_num: int, name: str):
        self.current_step = step_num - 1
        for i, step in enumerate(self.steps):
            if i < self.current_step:
                step.set_status("done")
            elif i == self.current_step:
                step.set_status("running")
            else:
                step.set_status("waiting")
        self.current_label.value = f"目前的：{name}"

    def add_log(self, msg: str, level: str = "info", is_success: bool | None = None):
        """PR refactor/unified-log-view: 改用 LogView.add() 統一處理等級顏色。

        Args:
            msg: log 文字
            level: debug/info/warning/error/system（從字串前綴自動推斷）
            is_success: 保留向後兼容（True=success、False=error、None=預設）
        """
        # 向後兼容 is_success 參數（map 到 level）
        if is_success is not None and level == "info":
            level = "system" if is_success else "error"
        # 從 msg 字串前綴推斷 level（向後兼容既有呼叫）
        if level == "info":
            if msg.startswith("▶"):
                level = "system"
            elif msg.startswith("✅"):
                level = "system"
            elif msg.startswith("❌"):
                level = "error"
        self.log_view.add(f">> {msg}", level=level)

    def finish_step(self, step_num: int, success: bool):
        self.steps[step_num - 1].set_status("done" if success else "failed")

    def finish_all(self, success: bool):
        if success:
            self.current_label.value = "✅ 一鍵製作完成！"
        else:
            self.current_label.value = "❌ 流程失敗"
        for step in self.steps:
            if step.status == "running":
                step.set_status("failed" if not success else "done")

    def hide(self):
        self.container.visible = False

    def clear_logs(self):
        # PR refactor/unified-log-view: log_view 改用 LogView
        self.log_view.clear()


# =============================================================================
# PipelineView - 流水線主視圖
# =============================================================================

class PipelineView(ft.Column):
    """模組流水線翻譯打包工作台視圖"""

    def __init__(self, page: ft.Page, file_picker: ft.FilePicker):
        super().__init__(expand=True, spacing=15)
        self._page = page
        self.file_picker = file_picker
        self.registry = None  # 預留給外部注入
        
        self.input_path_text = ft.TextField(
            hint_text="尚未選擇讀取來源...",
            expand=True,
            border_color=BLUE_700,
            text_size=12,
            dense=True,
        )
        self.output_path_text = ft.TextField(
            hint_text="尚未選擇輸出目的地...",
            expand=True,
            border_color=BLUE_700,
            text_size=12,
            dense=True,
        )
        self.log_content = ft.ListView(expand=True, spacing=5, auto_scroll=True)
        self.progress_bar = ft.ProgressBar(width=float("inf"), height=8, value=0, color=CYAN_400, bgcolor="#E0E0E0")
        self.progress_status = ft.Text("等待任務啟動...", size=12, color=GREY_600)
        self.keys_container = ft.Column(spacing=10)

        self.progress_panel = PipelineProgressPanel(page)

        self._lang_code_checks = {}

        self._build_ui()

    def set_view_registry(self, registry):
        """將全域視圖註冊表注入視圖內。"""
        self.registry = registry
        self._page.update()

    def set_registry(self, registry):
        """將全域視圖註冊表注入視圖內（相容舊代碼）。"""
        self.set_view_registry(registry)
        self.log_content.controls.append(
            ft.Text(f">> {msg}", color=RED_400 if is_err else CYAN_700, size=12, font_family="Consolas")
        )
        self._page.update()

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

    def _delete_key_field(self, row_obj):
        self.keys_container.controls.remove(row_obj)
        self._page.update()

    def _add_key_field(self, initial_value=""):
        new_row = ft.Row(spacing=10)
        key_tf = ft.TextField(
            value=initial_value,
            label=f"API Key {len(self.keys_container.controls) + 1}",
            expand=True,
            text_size=12,
            border_color=BLUE_700
        )
        del_btn = ft.IconButton(
            icon=ft.Icons.DELETE,
            icon_color=RED_400,
            on_click=lambda _: self._delete_key_field(new_row)
        )
        new_row.controls = [key_tf, del_btn]
        self.keys_container.controls.append(new_row)
        self._page.update()

    def _show_progress_panel(self):
        """顯示進度面板並清除舊日誌"""
        self.progress_panel.clear_logs()
        self.progress_panel.start()
        self._page.update()

    def _show_snack_bar(self, message: str, color: str = RED_400):
        log_info(f"Pipeline SnackBar: {message}")
        snack = ft.SnackBar(ft.Text(message), bgcolor=color)
        self._page.overlay.append(snack)
        snack.open = True
        self._page.update()

    # =============================================================================
    # Background Workers
    # =============================================================================

    def _run_extraction(self, mods_dir: str, output_dir: str, mode: str, lang_codes: list[str]):
        """執行抽取資源（背景執行緒）。

        Args:
            mods_dir: Mod 來源目錄
            output_dir: 輸出目錄
            mode: 執行模式（lang / book / dual）
            lang_codes: 語言代碼列表，傳入 extraction service
                       若為 None，service 會從 config 讀取預設值
        """
        session = TaskSession()
        self.progress_panel.set_step_running(1, "抽取資源")
        self.progress_panel.add_log(f"▶ 開始：抽取資源（{mode}）")

        def worker():
            try:
                os.makedirs(os.path.join(output_dir, "jar_mod_extract", "_提取lang_輸出"), exist_ok=True)
                os.makedirs(os.path.join(output_dir, "jar_mod_extract", "_提取book_輸出"), exist_ok=True)

                if mode in ("lang", "dual"):
                    lang_out = os.path.join(output_dir, "jar_mod_extract", "_提取lang_輸出")
                    run_lang_extraction_service(mods_dir, lang_out, session, lang_codes=lang_codes)
                    async def do_lang_log(_):
                        self.progress_panel.add_log("✅ Lang 抽取完成")
                    self._page.run_task(do_lang_log, None)

                if mode in ("book", "dual"):
                    book_out = os.path.join(output_dir, "jar_mod_extract", "_提取book_輸出")
                    run_book_extraction_service(mods_dir, book_out, session, lang_codes=lang_codes)
                    async def do_book_log(_):
                        self.progress_panel.add_log("✅ Book 抽取完成")
                    self._page.run_task(do_book_log, None)

                async def do_finish(_):
                    self.progress_panel.finish_step(1, not session.error)
                self._page.run_task(do_finish, None)

            except Exception as ex:
                async def do_err_log(_):
                    self.progress_panel.add_log(f"❌ 錯誤：{ex}", level="error")
                self._page.run_task(do_err_log, None)
                async def do_err_finish(_):
                    self.progress_panel.finish_step(1, False)
                self._page.run_task(do_err_finish, None)

        threading.Thread(target=worker, daemon=True).start()
        self._poll_session(session)

    def _run_merge(self, input_src, output_dir: str, input_mode: str, only_lang: bool, process_zh_cn: bool,
                   patchouli_skip: bool, patchouli_threshold: float, zh_en_threshold: int,
                   lang_codes: list[str]):
        session = TaskSession()
        self.progress_panel.set_step_running(2, "語系比對")
        display_input = ",".join(input_src) if isinstance(input_src, list) else input_src
        self.progress_panel.add_log(f"▶ 開始：語系比對（輸入：{display_input}）")

        def worker():
            try:
                os.makedirs(output_dir, exist_ok=True)
                if input_mode == "folder":
                    run_merge_folder_batch_service(
                        input_dir=input_src,
                        output_dir=output_dir,
                        session=session,
                        only_process_lang=only_lang,
                        process_zh_cn=process_zh_cn,
                        patchouli_skip=patchouli_skip,
                        patchouli_threshold=patchouli_threshold,
                        zh_en_threshold=zh_en_threshold,
                    )
                else:
                    merge_input_list = input_src if isinstance(input_src, list) else [input_src]
                    run_merge_zip_batch_service(
                        zip_paths=merge_input_list,
                        output_dir=output_dir,
                        session=session,
                        only_process_lang=only_lang,
                        process_zh_cn=process_zh_cn,
                        patchouli_skip=patchouli_skip,
                        patchouli_threshold=patchouli_threshold,
                        zh_en_threshold=zh_en_threshold,
                    )

                async def do_finish(_):
                    self.progress_panel.finish_step(2, not session.error)
                    if not session.error:
                        self.progress_panel.add_log("✅ 語系比對完成")
                self._page.run_task(do_finish, None)
            except Exception as ex:
                async def do_err(_):
                    self.progress_panel.add_log(f"❌ 錯誤：{ex}", level="error")
                self._page.run_task(do_err, None)

        threading.Thread(target=worker, daemon=True).start()
        self._poll_session(session)

    def _poll_session(self, session: TaskSession):
        """輪詢 session 直到完成"""
        def poll():
            while session.status in ("RUNNING", "IDLE"):
                time.sleep(0.5)
                snap = session.snapshot()
                progress = float(snap.get("progress", 0) or 0)
                async def do_progress(_):
                    self._update_progress(progress, f"{int(progress * 100)}%")
                self._page.run_task(do_progress, None)
                for log_entry in snap.get("logs", []):
                    async def do_log(_, le=log_entry):
                        self.progress_panel.add_log(le.text)
                    self._page.run_task(do_log, None)
            async def do_finish(_):
                self._update_progress(1.0, "完成")
            self._page.run_task(do_finish, None)

        threading.Thread(target=poll, daemon=True).start()

    # =============================================================================
    # Button Handlers
    # =============================================================================

    def _on_extract_click(self, e=None):
        input_val = (self.input_path_text.value or "").strip()
        output_val = (self.output_path_text.value or "").strip()
        if not input_val:
            self._show_snack_bar("⚠️ 請填寫 Mod 來源路徑")
            return
        if not output_val:
            self._show_snack_bar("⚠️ 請填寫輸出目錄路徑")
            return
        open_extract_dialog(
            page=self._page,
            file_picker=self.file_picker,
            input_path=input_val,
            output_path=output_val,
            on_run_extraction=self._run_extraction,
            lang_code_checks=self._lang_code_checks,
            show_snack_bar=self._show_snack_bar,
        )

    def _on_merge_click(self, e=None):
        input_val = (self.input_path_text.value or "").strip()
        output_val = (self.output_path_text.value or "").strip()
        if not input_val:
            self._show_snack_bar("⚠️ 請填寫 Mod 來源路徑")
            return
        if not output_val:
            self._show_snack_bar("⚠️ 請填寫輸出目錄路徑")
            return
        open_merge_dialog(
            page=self._page,
            file_picker=self.file_picker,
            input_path=input_val,
            output_path=output_val,
            lang_code_checks=self._lang_code_checks,
            on_run_merge=self._run_merge,
            show_snack_bar=self._show_snack_bar,
        )

    def _on_translate_click(self, e=None):
        input_val = (self.input_path_text.value or "").strip()
        output_val = (self.output_path_text.value or "").strip()
        if not input_val:
            self._show_snack_bar("⚠️ 請填寫 Mod 來源路徑")
            return
        if not output_val:
            self._show_snack_bar("⚠️ 請填寫輸出目錄路徑")
            return
        open_translate_dialog(
            page=self._page,
            file_picker=self.file_picker,
            input_path=input_val,
            output_path=output_val,
            on_start_translate=self._run_translate,
            show_snack_bar=self._show_snack_bar,
        )

    def _on_bundle_click(self, e=None):
        input_val = (self.input_path_text.value or "").strip()
        output_val = (self.output_path_text.value or "").strip()
        if not input_val:
            self._show_snack_bar("⚠️ 請填寫 Mod 來源路徑")
            return
        if not output_val:
            self._show_snack_bar("⚠️ 請填寫輸出目錄路徑")
            return
        open_bundle_dialog(
            page=self._page,
            file_picker=self.file_picker,
            input_path=input_val,
            output_path=output_val,
            on_start_bundle=self._run_bundle,
            show_snack_bar=self._show_snack_bar,
        )

    def _on_one_click_click(self, e=None):
        input_val = (self.input_path_text.value or "").strip()
        output_val = (self.output_path_text.value or "").strip()
        log_info(f"Pipeline one_click: input=[{input_val}], output=[{output_val}]")
        if not input_val:
            self._show_snack_bar("⚠️ 請輸入 Mod 來源路徑")
            return
        if not output_val:
            self._show_snack_bar("⚠️ 請輸入輸出目錄路徑")
            return
        open_one_click_dialog(
            page=self._page,
            file_picker=self.file_picker,
            input_path=input_val,
            output_path=output_val,
            on_execute=self._on_one_click_execute,
            show_snack_bar=self._show_snack_bar,
        )

    def _on_one_click_execute(self, config: dict):
        input_dir = (self.input_path_text.value or "").strip()
        output_dir = (self.output_path_text.value or "").strip()

        if not input_dir or not os.path.isdir(input_dir):
            self._show_snack_bar("❌ Mod 來源不存在或未選擇")
            return
        if not output_dir or not os.path.isdir(output_dir):
            self._show_snack_bar("❌ 輸出目錄不存在或未選擇")
            return

        cfg = PipelineConfig(input_dir, output_dir)
        mode = config.get("mode", "lang")
        lang_codes = config.get("lang_codes", [])

        self._show_progress_panel()
        self._set_buttons_disabled(True)

        def worker():
            try:
                session = TaskSession()

                def run_step(step_num, name, service_fn):
                    self.progress_panel.set_step_running(step_num, name)
                    self.progress_panel.add_log(f"▶ 開始：{name}")
                    t = threading.Thread(target=_run_step_worker, args=(service_fn, session), daemon=True)
                    t.start()
                    self._poll_session(session)
                    t.join()
                    if session.error:
                        self.progress_panel.add_log(f"❌ {name} 失敗", level="error")
                        return False
                    self.progress_panel.add_log(f"✅ {name} 完成")
                    return True

                def _run_step_worker(service_fn, session):
                    try:
                        service_fn()
                    except Exception as ex:
                        session.add_log(f"❌ 錯誤：{ex}")
                        session.set_error()

                if mode in ("lang", "dual"):
                    ok = run_step(1, "抽取資源（Lang）", lambda: run_lang_extraction_service(
                        cfg.input_dir, cfg.extract_lang_output_dir, session, lang_codes=lang_codes))
                    if not ok:
                        self._page.run_task(lambda _: self._reenable_buttons())
                        return

                if mode in ("book", "dual"):
                    ok = run_step(1, "抽取資源（Book）", lambda: run_book_extraction_service(
                        cfg.input_dir, cfg.extract_book_output_dir, session, lang_codes=lang_codes))
                    if not ok:
                        self._page.run_task(lambda _: self._reenable_buttons())
                        return

                ok = run_step(2, "語系比對", lambda: run_merge_zip_batch_service(
                    zip_paths=[cfg.merge_input_dir],
                    output_dir=output_dir,
                    session=session,
                    only_process_lang=True,
                    process_zh_cn=config.get("process_zh_cn", True),
                    patchouli_skip=config.get("patchouli_skip", False),
                    patchouli_threshold=config.get("patchouli_threshold", 0.5),
                    zh_en_threshold=config.get("zh_en_threshold", 2),
                ))
                if not ok:
                    self._page.run_task(lambda _: self._reenable_buttons())
                    return

                ok = run_step(3, "啟動翻譯", lambda: run_lm_translation_service(
                    input_dir=cfg.translate_input_dir,
                    output_dir=cfg.translate_output_dir,
                    session=session,
                    dry_run=config.get("dry_run", False),
                    export_lang=False,
                    write_new_cache=config.get("write_new_cache", True),
                ))
                if not ok:
                    self._page.run_task(lambda _: self._reenable_buttons())
                    return

                ok = run_step(4, "打包資源", lambda: self._do_bundle(
                    input_root_dir=cfg.bundle_input_dir,
                    output_zip_path=config.get("zip_output") or cfg.bundle_output_zip,
                    description=config.get("description", ""),
                    pack_image_path=config.get("pack_image"),
                    extra_folders=config.get("extra_folders", []),
                    session=session,
                ))
                if not ok:
                    self._page.run_task(lambda _: self._reenable_buttons())
                    return

                self.progress_panel.finish_all(True)
                async def done(_):
                    self.progress_panel.add_log("✅ 一鍵製作完成！")
                    self._reenable_buttons()
                self._page.run_task(done, None)

            except Exception as ex:
                async def fail(_):
                    self.progress_panel.add_log(f"❌ 流程失敗：{ex}", level="error")
                    self._reenable_buttons()
                self._page.run_task(fail, None)

        threading.Thread(target=worker, daemon=True).start()

    def _do_bundle(self, input_root_dir: str, output_zip_path: str, description: str,
                   pack_image_path, extra_folders: list, session: TaskSession):
        os.makedirs(os.path.dirname(output_zip_path), exist_ok=True)
        for update_dict in run_bundling_service(
            input_root_dir=input_root_dir,
            output_zip_path=output_zip_path,
            description=description,
            min_format=0,
            max_format=0,
            pack_image_path=pack_image_path,
            extra_folders=extra_folders or None,
        ):
            if update_dict.get("log"):
                session.add_log(update_dict["log"])
            if update_dict.get("progress") is not None:
                session.set_progress(update_dict["progress"])
            if update_dict.get("error"):
                session.set_error()
                return

    def _set_buttons_disabled(self, disabled: bool):
        for ctrl in self.workbench_view.controls:
            if isinstance(ctrl, ft.Column):
                for row in ctrl.controls:
                    if isinstance(row, ft.Row) and row.spacing == 10:
                        for btn in row.controls:
                            if isinstance(btn, ft.Button) and btn.height == 55:
                                btn.disabled = disabled

    def _reenable_buttons(self, e=None):
        self._set_buttons_disabled(False)
        self._page.update()

    def _run_translate(self, input_dir: str, output_dir: str, dry_run: bool, write_new_cache: bool):
        session = TaskSession()
        self.progress_panel.set_step_running(3, "啟動翻譯")
        self.progress_panel.add_log(f"▶ 開始：啟動翻譯（{'Dry-Run' if dry_run else '正式'}）")

        def worker():
            try:
                os.makedirs(output_dir, exist_ok=True)
                run_lm_translation_service(
                    input_dir=input_dir,
                    output_dir=output_dir,
                    session=session,
                    dry_run=dry_run,
                    export_lang=False,
                    write_new_cache=write_new_cache,
                )
                async def do_finish(_):
                    self.progress_panel.finish_step(3, not session.error)
                    if not session.error:
                        self.progress_panel.add_log("✅ 翻譯完成")
                self._page.run_task(do_finish, None)
            except Exception as ex:
                async def do_err(_):
                    self.progress_panel.add_log(f"❌ 錯誤：{ex}", level="error")
                self._page.run_task(do_err, None)
                async def do_err_finish(_):
                    self.progress_panel.finish_step(3, False)
                self._page.run_task(do_err_finish, None)

        threading.Thread(target=worker, daemon=True).start()
        self._poll_session(session)

    def _run_bundle(self, input_root_dir: str, output_zip_path: str, description: str,
                    min_format, max_format, pack_image_path: str | None, extra_folders: list[str]):
        session = TaskSession()
        self.progress_panel.set_step_running(4, "打包資源")
        self.progress_panel.add_log(f"▶ 開始：打包資源（{os.path.basename(output_zip_path)}）")

        def worker():
            try:
                os.makedirs(os.path.dirname(output_zip_path), exist_ok=True)
                for update_dict in run_bundling_service(
                    input_root_dir=input_root_dir,
                    output_zip_path=output_zip_path,
                    description=description,
                    min_format=min_format or 0,
                    max_format=max_format or 0,
                    pack_image_path=pack_image_path,
                    extra_folders=extra_folders or None,
                ):
                    if "log" in update_dict:
                        self.progress_panel.add_log(update_dict["log"])
                    if "progress" in update_dict and update_dict["progress"] is not None:
                        async def do_p(_):
                            self._update_progress(update_dict["progress"], f"{int(update_dict['progress'] * 100)}%")
                        self._page.run_task(do_p, None)
                    if update_dict.get("error"):
                        async def do_err(_):
                            self.progress_panel.finish_step(4, False)
                        self._page.run_task(do_err, None)
                        return
                async def do_finish(_):
                    self.progress_panel.finish_step(4, True)
                    self.progress_panel.add_log(f"✅ 打包完成：{os.path.basename(output_zip_path)}")
                self._page.run_task(do_finish, None)
            except Exception as ex:
                async def do_err(_):
                    self.progress_panel.add_log(f"❌ 錯誤：{ex}", level="error")
                self._page.run_task(do_err, None)
                async def do_err_finish(_):
                    self.progress_panel.finish_step(4, False)
                self._page.run_task(do_err_finish, None)

        threading.Thread(target=worker, daemon=True).start()
        self._poll_session(session)

    # =============================================================================
    # UI Layout
    # =============================================================================

    def _build_ui(self):
        self.workbench_view = ft.Column([
            ft.Text("翻譯工作台", size=24, weight="bold", color=BLUE_700),

            ft.Container(
                content=ft.Column([
                    ft.Text("1. 基礎與打包配置", weight="bold", color=BLUE_600),
                    ft.Row([
                        ft.Button("Mod 來源", icon=ft.Icons.FOLDER, on_click=lambda _: self._page.run_task(self._pick_input_dir)),
                        ft.Container(content=self.input_path_text, expand=True),
                    ]),
                    ft.Row([
                        ft.Button("輸出目錄", icon=ft.Icons.FOLDER_SPECIAL, on_click=lambda _: self._page.run_task(self._pick_output_dir)),
                        ft.Container(content=self.output_path_text, expand=True),
                    ]),
                ], spacing=10),
                bgcolor="surfaceContainerLow", padding=20, border_radius=15
            ),

            self.progress_panel.container,

            ft.Text("2. 執行任務", weight="bold", color=BLUE_600),
            ft.Column([
                ft.Row([
                    ft.Button("抽取資源", icon=ft.Icons.UNARCHIVE, expand=True, height=55, bgcolor=GREEN_700, color=WHITE, on_click=self._on_extract_click),
                    ft.Button("語系比對", icon=ft.Icons.SEARCH, expand=True, height=55, bgcolor=TEAL_700, color=WHITE, on_click=self._on_merge_click),
                    ft.Button("啟動翻譯", icon=ft.Icons.AUTO_AWESOME, expand=True, height=55, bgcolor=BLUE_700, color=WHITE, on_click=self._on_translate_click),
                    ft.Button("打包資源", icon=ft.Icons.INVENTORY_2, expand=True, height=55, bgcolor=PURPLE_700, color=WHITE, on_click=self._on_bundle_click),
                ], spacing=10),

                ft.Container(
                    content=ft.Column([
                        ft.Row([ft.Icon(ft.Icons.INFO, size=14, color=GREY_500), self.progress_status]),
                        self.progress_bar
                    ], spacing=5),
                    padding=5,
                ),

                ft.Button(
                    "一鍵製作 (自動執行所有流程)",
                    icon=ft.Icons.FLASH_ON,
                    width=float("inf"),
                    height=35,
                    bgcolor=YELLOW_900,
                    color=YELLOW,
                    on_click=self._on_one_click_click,
                ),
            ], spacing=15, expand=True)
        ])

        self.api_view = ft.Column([
            ft.Text("API 金鑰管理", size=24, weight="bold", color=ORANGE_700),
            ft.Container(content=self.keys_container, expand=True),
            ft.Button("儲存設定", icon=ft.Icons.SAVE, bgcolor=BLUE_700, color=WHITE),
        ], spacing=10, expand=True)

        self.controls.append(self.workbench_view)