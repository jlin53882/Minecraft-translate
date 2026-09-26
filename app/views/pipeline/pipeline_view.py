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

from app.logging.task_session import TaskSession
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
from app.ui.snack import show_snack
from app.ui.theme import (
    BLUE_50,
    BLUE_400,
    BLUE_600,
    BLUE_700,
    CYAN_400,
    GREEN_50,
    GREEN_600,
    GREEN_700,
    GREY_200,
    GREY_500,
    GREY_600,
    ORANGE_700,
    PURPLE_700,
    RED_50,
    RED_400,
    TEAL_700,
    WHITE,
    YELLOW,
    YELLOW_900,
)
from app.views._log import LogView
from app.views.pipeline.pipeline_bundle_dialog import open_bundle_dialog
from app.views.pipeline.pipeline_extract_dialog import open_extract_dialog
from app.views.pipeline.pipeline_merge_dialog import open_merge_dialog
from app.views.pipeline.pipeline_one_click_dialog import open_one_click_dialog
from app.views.pipeline.pipeline_translate_dialog import open_translate_dialog
from translation_tool.utils.config_manager import load_config
from translation_tool.utils.log_unit import log_error, log_info, log_warning


def _has_files(path: str) -> bool:
    """資料夾存在且至少含有一個檔案。"""
    if not os.path.isdir(path):
        return False
    return any(files for _, _, files in os.walk(path))


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
        self.organized_folder = lang_merger.get(
            "pending_organized_folder_name", "待翻譯整理需翻譯"
        )

        self.lm_translate = "lm_translate"
        self.translate_output_subfolder = "_翻譯輸出"

        self.output_zip_name = bundler.get("output_zip_name", "可使用翻譯.zip")

    @property
    def extract_lang_output_dir(self):
        return os.path.join(
            self.output_dir, self.jar_mod_extract, self.lang_output_subfolder
        )

    @property
    def extract_book_output_dir(self):
        return os.path.join(
            self.output_dir, self.jar_mod_extract, self.book_output_subfolder
        )

    @property
    def merge_input_dir(self):
        return os.path.join(self.output_dir, self.jar_mod_extract)

    @property
    def merge_output_dir(self):
        return os.path.join(
            self.output_dir, self.locale_sort, self.sort_output_subfolder
        )

    @property
    def translate_input_dir(self):
        """語言檔待翻譯清單（語系合併輸出於 lang_output/ 底下）。"""
        return os.path.join(self.merge_output_dir, "lang_output", self.organized_folder)

    @property
    def patchouli_pending_dir(self):
        """Patchouli 書本的待翻譯內容。"""
        return os.path.join(
            self.merge_output_dir, "patchouli_output", self.pending_folder
        )

    @property
    def translate_input_dirs(self):
        return [self.translate_input_dir, self.patchouli_pending_dir]

    @property
    def translate_output_dir(self):
        return os.path.join(
            self.output_dir, self.lm_translate, self.translate_output_subfolder
        )

    @property
    def bundle_input_dir(self):
        return os.path.join(
            self.output_dir, self.lm_translate, self.translate_output_subfolder
        )

    @property
    def bundle_staging_dir(self):
        return os.path.join(self.output_dir, "_打包暫存")

    @property
    def bundle_sources(self):
        """打包來源（優先序由低到高）：合併後的既有譯文 → LLM 新譯文。"""
        return [
            os.path.join(self.merge_output_dir, "lang_output"),
            os.path.join(self.merge_output_dir, "patchouli_output"),
            self.translate_output_dir,
        ]

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
            content=ft.Column(
                [
                    ft.Text("執行進度", weight="bold", color=BLUE_700),
                    self.step_row,
                    self.current_label,
                    ft.Divider(),
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.INFO, size=14, color=GREY_500),
                            ft.Text("步驟日誌：", size=12, color=GREY_600),
                        ]
                    ),
                    self.log_view,
                ],
                spacing=5,
            ),
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
            if msg.startswith(("▶", "✅")):
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
        self.log_content = ft.ListView(
            expand=True, spacing=5, auto_scroll=True
        )  # PR refactor/unified-log-view: dead widget, 將在下個 commit 移除
        self.progress_bar = ft.ProgressBar(
            width=float("inf"), height=8, value=0, color=CYAN_400, bgcolor="#E0E0E0"
        )
        self.progress_status = ft.Text("等待任務啟動...", size=12, color=GREY_600)
        self.keys_container = ft.Column(spacing=10)

        self.progress_panel = PipelineProgressPanel(page)

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
            border_color=BLUE_700,
        )
        del_btn = ft.IconButton(
            icon=ft.Icons.DELETE,
            icon_color=RED_400,
            on_click=lambda _: self._delete_key_field(new_row),
        )
        new_row.controls = [key_tf, del_btn]
        self.keys_container.controls.append(new_row)
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
        session = TaskSession()
        done = threading.Event()
        self._ui(self.progress_panel.set_step_running, step_num, name)
        self._ui(self.progress_panel.add_log, f"▶ 開始：{name}")
        watcher = self._page.run_task(self._watch_session, session, done)
        try:
            result = service_fn(session)
            if inspect.isgenerator(result):
                for _ in result:
                    pass
        except Exception as ex:  # noqa: BLE001 - 背景步驟邊界：任何錯誤都轉成步驟失敗
            log_error(f"[Pipeline] {name} 失敗：{ex}\n{traceback.format_exc()}")
            session.add_log(f"❌ 錯誤：{ex}", level="error")
            session.set_error()
        finally:
            done.set()
        try:
            if watcher is not None:
                watcher.result(timeout=30)
        except (
            concurrent.futures.TimeoutError,
            concurrent.futures.CancelledError,
        ) as ex:
            log_warning(f"[Pipeline] {name} 日誌同步未完成：{ex!r}")

        ok = not self._session_failed(session)

        def _finish():
            self.progress_panel.finish_step(step_num, ok)
            self.progress_panel.add_log(f"✅ {name} 完成" if ok else f"❌ {name} 失敗")
            self._update_progress(1.0, "完成" if ok else "失敗")

        self._ui(_finish)
        return ok

    def _start_single_step(self, step_num: int, name: str, service_fn):
        """單一步驟按鈕：顯示進度面板、停用按鈕，並在背景執行。"""
        self._show_progress_panel()
        self._set_buttons_disabled(True)
        self._page.update()

        def worker():
            try:
                self._run_session_step(step_num, name, service_fn)
            finally:
                self._ui(self._reenable_buttons)

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
        input_dir = (self.input_path_text.value or "").strip()
        output_dir = (self.output_path_text.value or "").strip()

        if not input_dir or not os.path.isdir(input_dir):
            show_snack(self._page, "❌ Mod 來源不存在或未選擇")
            return
        if not output_dir or not os.path.isdir(output_dir):
            show_snack(self._page, "❌ 輸出目錄不存在或未選擇")
            return

        mode = config.get("mode", "lang")
        lang_codes = config.get("lang_codes", [])
        if not lang_codes:
            show_snack(self._page, "⚠️ 請至少勾選一個語系代碼")
            return

        cfg = PipelineConfig(input_dir, output_dir)
        merge_options = {
            "output_dir": cfg.merge_output_dir,
            "process_zh_cn": config.get("process_zh_cn", True),
            "patchouli_skip": config.get("patchouli_skip", False),
            "patchouli_threshold": config.get("patchouli_threshold", 0.5),
            "zh_en_threshold": config.get("zh_en_threshold", 2),
        }

        self._show_progress_panel()
        self._set_buttons_disabled(True)
        self._page.update()

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
                sources.append((cfg.extract_lang_output_dir, True))
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
                min_format=0,
                max_format=0,
                pack_image_path=config.get("pack_image"),
                extra_folders=config.get("extra_folders", []),
            )

        steps = [
            (1, "抽取資源", extract),
            (2, "語系比對", merge),
            (3, "啟動翻譯", translate),
            (4, "打包資源", bundle),
        ]

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
                    self.progress_panel.finish_all(success)
                    if success:
                        self.progress_panel.add_log("✅ 一鍵製作完成！")
                    self._reenable_buttons()

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
        for ctrl in self.workbench_view.controls:
            if isinstance(ctrl, ft.Column):
                for row in ctrl.controls:
                    if isinstance(row, ft.Row) and row.spacing == 10:
                        for btn in row.controls:
                            if isinstance(btn, ft.Button) and btn.height == 55:
                                btn.disabled = disabled
        if self._one_click_button is not None:
            self._one_click_button.disabled = disabled

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

    # =============================================================================
    # UI Layout
    # =============================================================================

    def _build_one_click_button(self):
        self._one_click_button = ft.Button(
            "一鍵製作 (自動執行所有流程)",
            icon=ft.Icons.FLASH_ON,
            width=float("inf"),
            height=35,
            bgcolor=YELLOW_900,
            color=YELLOW,
            on_click=self._on_one_click_click,
        )
        return self._one_click_button

    def _build_ui(self):
        self.workbench_view = ft.Column(
            [
                ft.Text("翻譯工作台", size=24, weight="bold", color=BLUE_700),
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Text("1. 基礎與打包配置", weight="bold", color=BLUE_600),
                            ft.Row(
                                [
                                    ft.Button(
                                        "Mod 來源",
                                        icon=ft.Icons.FOLDER,
                                        on_click=lambda _: self._page.run_task(
                                            self._pick_input_dir
                                        ),
                                    ),
                                    ft.Container(
                                        content=self.input_path_text, expand=True
                                    ),
                                ]
                            ),
                            ft.Row(
                                [
                                    ft.Button(
                                        "輸出目錄",
                                        icon=ft.Icons.FOLDER_SPECIAL,
                                        on_click=lambda _: self._page.run_task(
                                            self._pick_output_dir
                                        ),
                                    ),
                                    ft.Container(
                                        content=self.output_path_text, expand=True
                                    ),
                                ]
                            ),
                        ],
                        spacing=10,
                    ),
                    bgcolor="surfaceContainerLow",
                    padding=20,
                    border_radius=15,
                ),
                self.progress_panel.container,
                ft.Text("2. 執行任務", weight="bold", color=BLUE_600),
                ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Button(
                                    "抽取資源",
                                    icon=ft.Icons.UNARCHIVE,
                                    expand=True,
                                    height=55,
                                    bgcolor=GREEN_700,
                                    color=WHITE,
                                    on_click=self._on_extract_click,
                                ),
                                ft.Button(
                                    "語系比對",
                                    icon=ft.Icons.SEARCH,
                                    expand=True,
                                    height=55,
                                    bgcolor=TEAL_700,
                                    color=WHITE,
                                    on_click=self._on_merge_click,
                                ),
                                ft.Button(
                                    "啟動翻譯",
                                    icon=ft.Icons.AUTO_AWESOME,
                                    expand=True,
                                    height=55,
                                    bgcolor=BLUE_700,
                                    color=WHITE,
                                    on_click=self._on_translate_click,
                                ),
                                ft.Button(
                                    "打包資源",
                                    icon=ft.Icons.INVENTORY_2,
                                    expand=True,
                                    height=55,
                                    bgcolor=PURPLE_700,
                                    color=WHITE,
                                    on_click=self._on_bundle_click,
                                ),
                            ],
                            spacing=10,
                        ),
                        ft.Container(
                            content=ft.Column(
                                [
                                    ft.Row(
                                        [
                                            ft.Icon(
                                                ft.Icons.INFO, size=14, color=GREY_500
                                            ),
                                            self.progress_status,
                                        ]
                                    ),
                                    self.progress_bar,
                                ],
                                spacing=5,
                            ),
                            padding=5,
                        ),
                        self._build_one_click_button(),
                    ],
                    spacing=15,
                    expand=True,
                ),
            ]
        )

        self.api_view = ft.Column(
            [
                ft.Text("API 金鑰管理", size=24, weight="bold", color=ORANGE_700),
                ft.Container(content=self.keys_container, expand=True),
                ft.Button(
                    "儲存設定", icon=ft.Icons.SAVE, bgcolor=BLUE_700, color=WHITE
                ),
            ],
            spacing=10,
            expand=True,
        )

        self.controls.append(self.workbench_view)
