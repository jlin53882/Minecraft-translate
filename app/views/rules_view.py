"""app/views/rules_view.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import re
import threading
from functools import lru_cache

import flet as ft

from app.services_impl.config_service import load_replace_rules
from app.tasks.operation_registry import (
    CancellationPolicy,
    ShutdownPolicy,
    launch_page_operation,
)
from app.ui.debounce import Debouncer
from app.ui.design import C
from app.ui.snack import show_snack
from app.ui.update_scope import update_regions
from app.views.rules.rules_actions import (
    calc_total_pages,
    confirm_discard_changes,
    launch_validate_and_save,
    start_reload_thread,
)
from app.views.rules.rules_filter import (
    RuleQuery,
    filter_rules,
    parse_query,
    rule_matches,
)
from app.views.rules.rules_state import RulesTableState
from app.views.rules.rules_table import (
    apply_row_status,
    row_delete_button,
    set_row_issues,
)
from app.views.rules.rules_table import create_rule_row as rules_create_row
from app.views.rules.rules_validation import (
    build_from_index,
    check_rule,
    rule_hint,
    translate_regex_error,
    validate_rule,
)
from app.views.rules.rules_widgets import RulesWidgetsMixin
from translation_tool.utils.log_unit import log_error
from translation_tool.utils.replace_rules_diagnostics import diagnose_rules
from translation_tool.utils.text_processor import apply_replace_rules, safe_convert_text


class RulesView(RulesWidgetsMixin, ft.Column):
    """RulesView 類別。

    用途：封裝與 RulesView 相關的狀態與行為。
    維護注意：修改公開方法前請確認外部呼叫點與相容性。
    """

    def __init__(self, page: ft.Page):
        """初始化 RulesView。

        參數：
            page: Flet Page 物件
        """
        super().__init__(expand=True, spacing=16)
        self._page = page

        # --- 分頁和數據狀態 ---
        self._state = RulesTableState()
        self.page_size = self._state.page_size
        self.current_page = self._state.current_page
        self.all_rules_data = []
        self.total_pages = self._state.total_pages

        # --- 搜尋狀態 ---
        # _query 是「目前的過濾條件」；search_results 永遠由它重算（_apply_filter），
        # 資料增刪、排序、重載後都要重算，否則畫面會顯示已刪除或過期的規則。
        self._query: RuleQuery | None = None
        self.search_results = None  # 符合的 rule 物件列表（None = 未過濾）
        self.search_keyword = ""
        self.search_regex = False

        # --- 未儲存變更與刪除復原 ---
        self._has_unsaved = False
        self._edit_seq = 0  # 每次改動遞增；儲存完成時用來判斷期間是否又有新的編輯
        # 診斷（交叉引用、重複等；非阻擋）：rid → [RuleIssue]，編輯後 debounce 重算
        self._issues: dict[int, list] = {}
        self._only_issues = False
        # s2twp 轉換是診斷最貴的部分；原詞字串相同就不必重轉
        self._convert_cached = lru_cache(maxsize=65536)(safe_convert_text)
        self._diag_debouncer = Debouncer(lambda: self.page, 0.6)
        self._row_status: dict[
            int, tuple[str, str, str]
        ] = {}  # rid → (欄位, 錯誤, 提醒)

        # 背景執行緒 → UI 的暫存佇列（掛載前完成的載入）
        self._ui_lock = threading.Lock()
        self._pending_ui_calls: list = []
        # debounce 在 event loop 上執行（threading.Timer 會在背景執行緒改控制項）
        self._search_debouncer = Debouncer(lambda: self.page, 0.3)

        # RID 序號生成器 (UI 專用 ID)
        self._rid_seq = self._state.rid_seq

        # --- UI 控制項初始化 (預先建立需參照的控制項) ---
        self._init_controls()

        # --- UI 結構建構 ---
        self.controls = [
            self._build_header(),
            self._build_toolbar(),
            ft.Row(
                [self._build_rules_table_area(), self._build_test_panel()],
                spacing=16,
                expand=True,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
        ]

        # 啟動背景載入
        self._initial_load()

    def _new_rid(self) -> int:
        """生成一個新的唯一 RID"""
        self._rid_seq += 1
        return self._rid_seq

    def _find_index_by_rid(self, rid: int) -> int:
        """透過 RID 找回資料在 all_rules_data 中的索引"""
        for i, r in enumerate(self.all_rules_data):
            if r.get("_rid") == rid:
                return i
        return -1

    def _sync_page_jump_field(self):
        """同步分頁跳轉欄位。"""
        if hasattr(self, "page_jump_field"):
            self.page_jump_field.value = str(self.current_page)
            try:
                if self.page_jump_field.page:
                    self.page_jump_field.update()
            except RuntimeError:
                pass

    def on_page_jump_submit(self, e):
        """驗證並執行頁碼跳轉"""
        raw = (e.control.value or "").strip()
        if not raw:
            show_snack(self.page, "請輸入頁碼", C.EM, text_color=C.ON_EM)
            self._sync_page_jump_field()
            return

        try:
            page = int(raw)
        except ValueError:
            show_snack(self.page, "頁碼必須是數字", C.RED, text_color=C.ON_EM)
            self._sync_page_jump_field()
            return

        if page < 1 or page > self.total_pages:
            show_snack(
                self.page,
                f"頁碼範圍：1 ~ {self.total_pages}",
                C.RED,
                text_color=C.ON_EM,
            )
            self._sync_page_jump_field()
            return

        self.current_page = page
        self._render_current_page()
        show_snack(self.page, f"已跳至第 {page} 頁", C.EM, text_color=C.ON_EM)
        self._sync_page_jump_field()

    # --- 即時測試 ---
    def on_test_change(self, e=None):
        """輸入變更時，用目前（尚未儲存）的規則套用到測試文字。"""
        text = self.test_input.value or ""
        if not text.strip():
            self.test_result.value = ""
            self.test_info.value = ""
        else:
            rules = [
                {"from": r.get("from", ""), "to": r.get("to", "")}
                for r in self.all_rules_data
                if (r.get("from") or "").strip()
                and self._is_compilable(r.get("from", ""))
            ]
            try:
                result = apply_replace_rules(text, rules)
            except Exception as err:  # noqa: BLE001 - 規則有問題時顯示原因，不讓頁面出錯
                result = text
                self.test_info.value = f"套用失敗：{err}"
                self.test_info.color = C.RED
            else:
                self.test_result.value = result
                changed = result != text
                self.test_info.value = (
                    f"已套用 {len(rules)} 條規則，文字有變更"
                    if changed
                    else f"已套用 {len(rules)} 條規則，沒有符合的規則"
                )
                self.test_info.color = C.EM if changed else C.DIM
            self.test_result.value = result
        try:
            self.test_result.update()
            self.test_info.update()
        except (AssertionError, RuntimeError):
            pass

    @staticmethod
    def _is_compilable(pattern: str) -> bool:
        try:
            re.compile(pattern)
        except re.error:
            return False
        return True

    # --- 邏輯功能 ---
    def on_sort_change(self, e):
        """根據選擇的排序模式重新排序規則資料（搜尋中也會連同結果一起重排）。"""
        mode = e.control.value
        if mode == "from_asc":
            self.all_rules_data.sort(key=lambda r: r.get("from", ""))
            label = "依 From 字典序"
        elif mode == "from_len":
            self.all_rules_data.sort(key=lambda r: len(r.get("from", "")))
            label = "依 From 長度"
        else:
            return
        self._diag_debouncer.call(self._rediagnose_after_edit)
        self._apply_filter()
        self.current_page = 1
        self._render_current_page()
        show_snack(self.page, f"✅ 已排序：{label}", C.EM, text_color=C.ON_EM)

    def on_search(self, e: ft.ControlEvent):
        """搜尋框輸入：清空立即還原，其餘 300ms debounce 後才搜尋。"""
        keyword = e.control.value

        if not (keyword or "").strip():
            self._search_debouncer.cancel()
            self._do_search("")
            return

        self._search_debouncer.call(self._do_search, keyword)

    def _do_search(self, keyword: str):
        """執行實際搜尋（Debounce 觸發）。"""
        query = parse_query(keyword)
        self._query = query
        self.search_keyword = query.keyword if query else ""
        self.search_regex = bool(query and query.regex)
        self._apply_filter()
        self.current_page = 1
        self._render_current_page()

        if query is None:
            show_snack(self.page, "已清除搜尋，顯示全部規則", C.EM, text_color=C.ON_EM)
        elif query.regex_invalid:
            show_snack(
                self.page,
                "正則語法有誤，改以一般文字搜尋",
                C.GOLD,
                text_color=C.ON_EM,
            )
        elif not self.search_results:
            show_snack(self.page, "找不到符合的規則", C.GOLD, text_color=C.ON_EM)
        else:
            mode_text = "（正則）" if query.regex else ""
            show_snack(
                self.page,
                f"找到 {len(self.search_results)} 條符合的規則{mode_text}",
                C.EM,
                text_color=C.ON_EM,
            )

    def _apply_filter(self):
        """依目前查詢與「只看有問題」重算 search_results（保持與 all_rules_data 同步）。"""
        results = filter_rules(self.all_rules_data, self._query)
        if self._only_issues:
            base = self.all_rules_data if results is None else results
            results = [r for r in base if r.get("_rid") in self._issues]
        self.search_results = results

    def _run_diagnostics(self):
        """重跑規則診斷（載入時同步執行；其他異動交給 debounce，避免大量規則時拖慢熱操作）。"""
        rules = self.all_rules_data
        found = diagnose_rules(rules, self._convert_cached)
        self._issues = {rules[i].get("_rid"): issues for i, issues in found.items()}
        self.only_issues_box.label = f"只看有問題（{len(self._issues)}）"

    def _rediagnose_after_edit(self):
        """編輯停頓後重算診斷並刷新畫面上各列的警示圖示（不重新過濾，避免列在輸入中消失）。"""
        self._run_diagnostics()
        touched = [self.only_issues_box]
        for row in self.rules_table.rows:
            if row.visible and row.data is not None:
                set_row_issues(row, [i.message for i in self._issues.get(row.data, ())])
                touched.append(row.cells[3].content)
        update_regions(*touched)

    def on_only_issues_change(self, e):
        """切換「只看有問題的規則」。"""
        self._only_issues = bool(e.control.value)
        self._apply_filter()
        self.current_page = 1
        self._render_current_page()
        self._scroll_table_to_top()

    def _clear_search(self):
        """清掉搜尋條件與搜尋框文字（不渲染、不提示；呼叫端負責渲染）。"""
        self._search_debouncer.cancel()
        self._query = None
        self.search_results = None
        self.search_keyword = ""
        self.search_regex = False
        self.search_box.value = ""
        self._only_issues = False
        self.only_issues_box.value = False

    def _rule_matches(self, rule: dict, keyword: str, use_regex: bool) -> bool:
        """檢查規則是否符合搜尋條件"""
        query = parse_query(f"/{keyword}/" if use_regex else keyword)
        return query is not None and rule_matches(rule, query)

    # ---------------------------------------------
    # 規則驗證模組
    # ---------------------------------------------

    def validate_rule(
        self, src: str, dst: str, all_rules, current_index, from_index=None
    ):
        """驗證規則格式正確性，回傳 (is_valid: bool, msg: str)。"""
        return validate_rule(src, dst, all_rules, current_index, from_index)

    def translate_regex_error(self, err) -> str:
        """翻譯正則表達式錯誤訊息"""
        return translate_regex_error(err)

    # --- 執行緒輔助與載入 ---

    def _run_on_ui_thread(self, func, *args, **kwargs):
        """在 UI 執行緒（Flet event loop）上安全執行函式。

        尚未掛上頁面時先暫存，等 did_mount 再執行，避免背景載入比掛載更早完成時
        結果被丟掉或在背景執行緒直接改控制項。
        """
        with self._ui_lock:
            try:
                page = self.page
            except RuntimeError:
                page = None
            loop = getattr(page, "loop", None) if page else None
            if loop is None:
                self._pending_ui_calls.append((func, args, kwargs))
                return
        loop.call_soon_threadsafe(lambda: func(*args, **kwargs))

    def will_unmount(self):
        """換頁／關閉：取消尚未執行的搜尋 debounce（idempotent）。"""
        self._search_debouncer.cancel()
        self._diag_debouncer.cancel()

    def did_mount(self):
        """掛上頁面後執行在掛載前排入的 UI 更新。"""
        with self._ui_lock:
            pending, self._pending_ui_calls = self._pending_ui_calls, []
        for func, args, kwargs in pending:
            func(*args, **kwargs)

    def _load_rules_core(self, *, force_reload: bool = False):
        """從共用 Store 載入替換規則；手動重新載入時強制讀取檔案。"""
        if force_reload:
            return load_replace_rules(force_reload=True, source="rules_manual_reload")
        return load_replace_rules()

    def _initial_load(self):
        """初次啟動時從檔案載入規則並渲染"""

        def run():
            # 背景執行緒只負責讀檔；渲染與 page.update() 交給 event loop
            try:
                rules_data = self._load_rules_core()
            except Exception as err:  # noqa: BLE001 - 失敗要顯示在 UI
                log_error(f"[Rules] 載入規則失敗：{err!r}", exc_info=True)
                self._run_on_ui_thread(self._handle_reload_failure, err)
                return
            self._run_on_ui_thread(self._handle_reload_success, rules_data)

        launch_page_operation(
            self.page,
            run,
            name="規則初始載入",
            owner="rules",
            cancellation=CancellationPolicy.NON_CANCELLABLE,
            shutdown=ShutdownPolicy.DRAIN_ONLY,
        )

    # --- 分頁渲染邏輯 ---

    def _display_rules(self) -> list:
        """目前畫面的資料來源：搜尋結果，或全部規則。"""
        if self.search_results is not None:
            return self.search_results
        return self.all_rules_data

    def _render_current_page(self):
        """根據當前頁碼渲染規則表格（支援搜尋結果過濾）。"""
        display_data = self._display_rules()
        total_count = len(display_data)
        self.total_pages = calc_total_pages(total_count, self.page_size)
        self.current_page = max(1, min(self.current_page, self.total_pages))

        start = (self.current_page - 1) * self.page_size
        current_page_data = display_data[start : start + self.page_size]

        # 搜尋中的「#」顯示規則在全部清單中的位置，與「第 N 條規則重複／錯誤」一致
        positions = None
        if self.search_results is not None:
            positions = {r.get("_rid"): i for i, r in enumerate(self.all_rules_data)}

        # 重用既有的表格列，只更新內容：
        # 每次都清空重建 50 列（各含兩個多行 TextField）時，Flet 需移除並重新序列化
        # 所有控制項，3 萬條規則下排序 / 清除搜尋單次約 2 秒；重用後只送出變更的值。
        # 多出來的列（例如搜尋結果較少時）只隱藏不刪除，清除搜尋時不必重建。
        rows = self.rules_table.rows
        needed = len(current_page_data)
        while len(rows) < needed:
            rows.append(self.create_rule_row("", "", 0, 0))
        for extra in rows[needed:]:
            extra.visible = False
            extra.data = None

        for offset, rule in enumerate(current_page_data):
            if "_rid" not in rule:
                rule["_rid"] = self._new_rid()
            rid = rule["_rid"]
            display_no = positions[rid] + 1 if positions else start + offset + 1
            self._fill_rule_row(
                rows[offset],
                rule.get("from", ""),
                rule.get("to", ""),
                rid,
                display_no=display_no,
                issue_messages=[i.message for i in self._issues.get(rid, ())],
            )
        self._row_status.clear()
        self._refresh_row_status(push=False)

        self._refresh_footer()
        self.prev_button.disabled = self.current_page == 1
        self.next_button.disabled = self.current_page == self.total_pages

        # 只刷新規則頁本身（而非整頁 diff）；尚未掛載時退回整頁更新
        try:
            self.update()
        except (AssertionError, RuntimeError):
            self.page.update()

    def _refresh_footer(self):
        """更新頁碼、規則數與「未儲存」標示的文字（不送出更新）。"""
        total_rules = len(self.all_rules_data)
        if self.search_results is None:
            count_text = f"共 {total_rules} 條規則"
        else:
            count_text = f"符合 {len(self.search_results)} 條（全部 {total_rules} 條）"
        self.page_info.value = f"頁面 {self.current_page} / {self.total_pages}"
        self.total_pages_text_label.value = f"/ {self.total_pages} 頁"
        self.total_count_text.value = count_text
        self.unsaved_text.visible = self._has_unsaved
        self._sync_page_jump_field()

    def _mark_changed(self):
        """記錄「有尚未儲存的變更」；第一次出現時顯示標示。"""
        self._edit_seq += 1
        if not self._has_unsaved:
            self._has_unsaved = True
            self.unsaved_text.visible = True
            update_regions(self.unsaved_text)

    def _clear_unsaved(self):
        """儲存或重新載入後，磁碟與畫面一致。"""
        self._has_unsaved = False
        self.unsaved_text.visible = False

    def _scroll_table_to_top(self):
        """換頁後把表格捲回頂端（捲動位置會跨頁保留，否則會停在上一頁的底部）。"""
        run_task = getattr(self.page, "run_task", None)
        if run_task is None:
            return
        try:
            run_task(self.rules_list.scroll_to, offset=0)
        except Exception:  # noqa: BLE001 - 捲動只是輔助，失敗不影響編輯
            log_error("[Rules] 表格捲回頂端失敗", exc_info=True)

    def _go_to_page(self, page: int):
        """換頁並把表格捲回頂端。"""
        self.current_page = page
        self._render_current_page()
        self._scroll_table_to_top()

    # --- 互動事件處理 ---

    def on_text_change(self, e):
        """當文字輸入變更時更新資料並即時驗證。"""
        rid = e.control.data["rid"]
        field = e.control.data["field"]

        index = self._find_index_by_rid(rid)
        if index < 0 or self.all_rules_data[index].get(field) == e.control.value:
            return
        self.all_rules_data[index][field] = e.control.value
        self._mark_changed()
        self._refresh_row_status()
        self._diag_debouncer.call(self._rediagnose_after_edit)

    def _refresh_row_status(self, push: bool = True):
        """重算畫面上各列的驗證狀態；只有結果變了的列才改樣式（push=True 時送出更新）。

        一次掃描建立 {from: [索引]} 與 {rid: 索引}，整頁 50 列的重複檢查是 O(n)。
        """
        rules = self.all_rules_data
        from_index = build_from_index(rules)
        positions = {r.get("_rid"): i for i, r in enumerate(rules)}
        touched = []
        for row in self.rules_table.rows:
            rid = row.data
            if not row.visible or rid is None or rid not in positions:
                continue
            idx = positions[rid]
            src, dst = rules[idx].get("from", ""), rules[idx].get("to", "")
            problem = check_rule(src, dst, rules, idx, from_index)
            status = (
                ("", "", rule_hint(src, dst)) if problem is None else (*problem, "")
            )
            if self._row_status.get(rid) == status:
                continue
            self._row_status[rid] = status
            apply_row_status(row, *status)
            touched.extend((row.cells[1].content, row.cells[2].content))
        if push:
            update_regions(*touched)

    @staticmethod
    def _fill_rule_row(
        row, from_text, to_text, rid: int, display_no: int, issue_messages=()
    ):
        """把既有的表格列改成顯示指定規則（清除先前的驗證樣式）。"""
        row.visible = True
        row.data = rid
        number_cell, from_cell, to_cell, _ = row.cells
        number_cell.content.value = str(display_no)
        for field, name, value in (
            (from_cell.content, "from", from_text),
            (to_cell.content, "to", to_text),
        ):
            field.value = value
            field.data = {"rid": rid, "field": name}
            field.border_color = None
            field.error = None
            field.helper = None
        row_delete_button(row).data = rid
        set_row_issues(row, issue_messages)

    def create_rule_row(self, from_text, to_text, rid: int, display_no: int):
        """建立規則編輯列 UI 元件"""
        return rules_create_row(self, from_text, to_text, rid, display_no)

    # --- 操作邏輯 ---

    def reload_rules_clicked(self, e):
        """重新載入；有未儲存的變更時先確認，避免編輯被靜默覆蓋。"""
        if not self._has_unsaved:
            return start_reload_thread(self)
        confirm_discard_changes(self.page, lambda: start_reload_thread(self))

    def _handle_reload_success(self, rules_data):
        """處理規則重新載入成功後的資料初始化與渲染"""
        self.all_rules_data = rules_data
        for r in self.all_rules_data:
            if "_rid" not in r:
                r["_rid"] = self._new_rid()

        self._clear_unsaved()
        self._run_diagnostics()
        self._apply_filter()  # 搜尋條件保留，但結果要換成新載入的規則物件
        self.current_page = 1
        self.loading_indicator.visible = False
        self._render_current_page()
        show_snack(self.page, "規則載入完成！", C.EM, text_color=C.ON_EM)

    def _handle_reload_failure(self, err):
        """處理規則重新載入失敗的錯誤顯示"""
        self.loading_indicator.visible = False
        update_regions(self.loading_indicator)
        show_snack(self.page, f"載入規則時發生錯誤: {err}", C.RED, text_color=C.ON_EM)

    def prev_page(self, e):
        """上一頁，若已在首頁則顯示提示"""
        if self.current_page > 1:
            self._go_to_page(self.current_page - 1)
        else:
            show_snack(self.page, "已在第一頁", C.EM, text_color=C.ON_EM)

    def next_page(self, e):
        """下一頁，若已在末頁則顯示提示"""
        if self.current_page < self.total_pages:
            self._go_to_page(self.current_page + 1)
        else:
            show_snack(self.page, "已在最後一頁", C.EM, text_color=C.ON_EM)

    @staticmethod
    def _build_from_index(all_rules) -> dict:
        """建立 {from: [索引...]}，供批次驗證時 O(1) 查重複。"""
        return build_from_index(all_rules)

    def _validate_all(self, rules):
        """驗證全部規則；回傳 (錯誤索引, 訊息) 或 None。可在背景執行緒執行。"""
        from_index = build_from_index(rules)
        for idx, rule in enumerate(rules):
            ok, msg = validate_rule(
                rule["from"], rule["to"], rules, idx, from_index=from_index
            )
            if not ok:
                return idx, msg
        return None

    def _reveal_rule(self, rid) -> bool:
        """跳到顯示指定規則的那一頁；規則不在搜尋結果內時先清除搜尋。"""
        if self.search_results is not None and not any(
            r.get("_rid") == rid for r in self.search_results
        ):
            self._clear_search()
        for pos, rule in enumerate(self._display_rules()):
            if rule.get("_rid") == rid:
                self.current_page = pos // self.page_size + 1
                return True
        return False

    def save_rules_clicked(self, e):
        """儲存規則點擊事件：驗證（背景執行緒）→ 儲存。

        3 萬條規則時舊版逐條掃描整個清單查重複（O(n²)），UI 約 87 秒無回應。
        """
        if getattr(self, "_saving", False):
            return
        snapshot = [dict(r) for r in self.all_rules_data]
        seq_at_click = self._edit_seq

        def finish(result):
            self._saving = False
            failure = result.get("failure")
            if failure is not None:
                idx, msg = failure
                show_snack(
                    self.page,
                    f"第 {idx + 1} 條規則錯誤：{msg}",
                    C.RED,
                    text_color=C.ON_EM,
                )
                self._reveal_rule(snapshot[idx].get("_rid"))
                self._render_current_page()
                return
            if result.get("error") is not None:
                show_snack(
                    self.page,
                    f"儲存規則時發生錯誤：{result['error']}",
                    C.RED,
                    text_color=C.ON_EM,
                )
                return
            if self._edit_seq == seq_at_click:  # 儲存期間又編輯的話仍算未儲存
                self._clear_unsaved()
                update_regions(self.unsaved_text)
            show_snack(self.page, "規則已成功儲存！", C.EM, text_color=C.ON_EM)

        run_task = getattr(self.page, "run_task", None)
        self._saving = True
        show_snack(self.page, "🔎 正在驗證並儲存規則…", C.EM, text_color=C.ON_EM)
        self._launch_rule_validation(snapshot, finish, run_task)

    def _launch_rule_validation(self, snapshot, finish, run_task):
        """驗證與寫檔的 worker 生命週期由 rules_actions 負責。"""
        launch_validate_and_save(self, snapshot, finish, run_task)

    def add_row_clicked(self, e):
        """新增一列空白規則，跳到最後一頁（搜尋中會先清除搜尋，否則新列不會顯示）。"""
        cleared = self._query is not None or self._only_issues
        if cleared:
            self._clear_search()
        rule = {"from": "", "to": "", "_rid": self._new_rid()}
        self.all_rules_data.append(rule)
        self._mark_changed()
        self.current_page = calc_total_pages(len(self.all_rules_data), self.page_size)
        self._render_current_page()
        self._focus_new_rule(rule["_rid"])
        suffix = "，並清除搜尋" if cleared else ""
        show_snack(
            self.page,
            f"➕ 已新增一條規則（已跳至最後一頁{suffix}）",
            C.EM,
            text_color=C.ON_EM,
        )

    def _focus_new_rule(self, rid: int):
        """把游標放進新規則列的 from 欄，可以直接打字。"""
        run_task = getattr(self.page, "run_task", None)
        row = next((r for r in self.rules_table.rows if r.data == rid), None)
        if run_task is None or row is None:
            return
        try:
            run_task(row.cells[1].content.focus)
        except Exception:  # noqa: BLE001 - 取得焦點只是輔助
            log_error("[Rules] 新規則列取得焦點失敗", exc_info=True)

    def delete_row_clicked(self, e):
        """刪除指定 RID 的規則並重新渲染；提示上附「復原」。"""
        idx = self._find_index_by_rid(e.control.data)
        if idx < 0:
            return
        rule = self.all_rules_data.pop(idx)
        self._mark_changed()
        self._diag_debouncer.call(self._rediagnose_after_edit)
        self._apply_filter()  # 搜尋結果也要拿掉，否則已刪除的列仍留在畫面上
        self._render_current_page()

        src = (rule.get("from") or "").strip()
        dst = (rule.get("to") or "").strip()
        src_preview = src[:20] + ("…" if len(src) > 20 else "")
        dst_preview = dst[:20] + ("…" if len(dst) > 20 else "")
        show_snack(
            self.page,
            f"🗑 已刪除：{src_preview} → {dst_preview}",
            C.RED,
            text_color=C.ON_EM,
            action_label="復原",
            on_action=lambda _e: self._undo_delete(idx, rule),
        )

    def _undo_delete(self, index: int, rule: dict):
        """把剛刪除的規則放回原位置，並跳到它所在的頁面。"""
        if any(r.get("_rid") == rule.get("_rid") for r in self.all_rules_data):
            return
        self.all_rules_data.insert(min(index, len(self.all_rules_data)), rule)
        self._mark_changed()
        self._diag_debouncer.call(self._rediagnose_after_edit)
        self._apply_filter()
        if not self._reveal_rule(rule["_rid"]):
            self.current_page = 1  # 搜尋條件不含它：留在搜尋結果的第 1 頁
        self._render_current_page()
        show_snack(self.page, "已復原刪除的規則", C.EM, text_color=C.ON_EM)

    @property
    def page(self):
        return self._page
