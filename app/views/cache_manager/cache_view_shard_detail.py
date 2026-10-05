"""CacheView 分片詳情的 SRC 預覽與 DST 編輯。（由 cache_view_shard.py 拆出，#114）"""

import json

from app.services_impl.cache.cache_services import (
    cache_get_entry_service,
    cache_save_all_service,
    cache_update_dst_service,
)

# UI 共用元件：總覽區使用新 UI kit。
from app.ui.design import C
from app.ui.snack import show_snack


class CacheShardDetailMixin:
    """CacheView 分片詳情的 SRC 預覽與 DST 編輯。（由 cache_view_shard.py 拆出，#114）"""

    def _dynamic_shard_src_height(self) -> int:
        """計算 shard src height（跟視窗 height 自適應）。

        規則：
        - 若 page.height 取得失敗或 <= 0 → 180
        - 正常：int(page.height * 0.24)，clamp 120..320
        """
        try:
            h = float(getattr(self.page, "height", 0) or 0)
        except Exception:  # noqa: BLE001
            h = 0

        if h <= 0:
            return 180

        return max(120, min(320, int(h * 0.24)))

    def _dynamic_shard_dst_height(self) -> int:
        """計算 shard dst height（跟視窗 height 自適應）。

        規則：
        - 若 page.height 取得失敗或 <= 0 → 180
        - 正常：int(page.height * 0.24)，clamp 120..320
        """
        try:
            h = float(getattr(self.page, "height", 0) or 0)
        except Exception:  # noqa: BLE001
            h = 0

        if h <= 0:
            return 180

        return max(120, min(320, int(h * 0.24)))

    def _format_shard_src_text(self, src_text: str, mode: str) -> str:
        """格式化來源文字（raw 或 preview 模式）"""
        src = str(src_text or "")
        if mode == "raw":
            return json.dumps(src, ensure_ascii=False)
        return src.replace("\\r\\n", "\n").replace("\\n", "\n")

    def _render_shard_src_panel(self):
        """渲染分片來源內容面板"""
        if not hasattr(self, "shard_src_field"):
            return

        if (
            not self.shard_detail_selected_type
            or not self.shard_detail_selected_file
            or not self.shard_detail_selected_key
        ):
            self.shard_src_meta.value = "SRC：請先選擇 key"
            self.shard_src_field.value = ""
            self._refresh_disabled_state()
            return

        ctype = self.shard_detail_selected_type
        key = self.shard_detail_selected_key
        filename = self.shard_detail_selected_file

        entry = cache_get_entry_service(ctype, key)
        if not isinstance(entry, dict):
            entry = self._load_shard_entry(ctype, filename, key)

        src_text = ""
        if isinstance(entry, dict):
            src_text = str(entry.get("src", ""))

        mode_text = (
            "👁️ 預覽" if self.shard_detail_src_mode == "preview" else "</> 原始碼"
        )
        self.shard_src_meta.value = f"SRC：{key} | 模式：{mode_text}"
        self.shard_src_field.value = self._format_shard_src_text(
            src_text, self.shard_detail_src_mode
        )
        self._refresh_disabled_state()

    def _on_shard_src_preview_mode(self, e):
        """切換來源預覽模式"""
        self.shard_detail_src_mode = "preview"

    def _on_shard_src_raw_mode(self, e):
        """切換來源原始模式"""
        self.shard_detail_src_mode = "raw"
        self._render_shard_src_panel()
        if self.page:
            self.update()

    def _render_shard_dst_panel(self):
        """渲染分片目標內容面板"""
        if not hasattr(self, "shard_dst_field"):
            return

        ctype = str(self.shard_detail_selected_type or "")
        filename = str(self.shard_detail_selected_file or "")
        key = str(self.shard_detail_selected_key or "")

        if not ctype or not filename or not key:
            self.shard_dst_loaded_sig = None
            self.shard_dst_original = ""
            self.shard_dst_meta.value = "DST：請先選擇 key"
            self.shard_dst_field.value = ""
            self._refresh_disabled_state()
            return

        current_sig = (ctype, filename, key)
        if self.shard_dst_loaded_sig != current_sig:
            entry = cache_get_entry_service(ctype, key)
            if not isinstance(entry, dict):
                entry = self._load_shard_entry(ctype, filename, key)

            dst_text = ""
            if isinstance(entry, dict):
                dst_text = self._normalize_cache_text(str(entry.get("dst", "")))

            self.shard_dst_original = dst_text
            self.shard_dst_field.value = dst_text
            self.shard_dst_loaded_sig = current_sig

        self.shard_dst_meta.value = f"DST：{key}"
        self._refresh_disabled_state()

    def _on_shard_dst_apply(self, e):
        """套用目標翻譯到快取"""
        if self.ui_busy:
            self._notify("目前忙碌中，暫停套用", "warn")
            return

        ctype = str(self.shard_detail_selected_type or "")
        filename = str(self.shard_detail_selected_file or "")
        key = str(self.shard_detail_selected_key or "")
        if not ctype or not filename or not key:
            self._notify("請先選擇分片與 key", "warn")
            return

        old_dst = str(self.shard_dst_original or "")
        new_dst = str(self.shard_dst_field.value or "")

        try:
            done = cache_update_dst_service(ctype, key, new_dst)
            if not done:
                self._notify("套用失敗：找不到目標 key", "error")
                return

            cache_save_all_service(write_new_shard=False, only_types=[ctype])

            history_event = {
                "ts": self._history_now_ts(),
                "cache_type": ctype,
                "key": key,
                "shard": filename,
                "old_dst": old_dst,
                "new_dst": new_dst,
                "action": "apply_from_shard_detail",
                "actor": "cache_shard_detail_ui",
            }
            self._history_append_event(ctype, history_event)

            self.shard_dst_original = new_dst
            if self.shard_dst_loaded_sig != (ctype, filename, key):
                self.shard_dst_loaded_sig = (ctype, filename, key)

            for row in self.query_results:
                if row.get("cache_type") == ctype and row.get("key") == key:
                    row["preview"] = new_dst
                    break

            self._render_query_results()
            self._render_query_detail()
            self._render_shard_src_panel()
            self._render_shard_dst_panel()
            self._notify("已套用 C3 DST 並寫入快取", "info")
            if self.page:
                self.page.update()
        except Exception as ex:  # noqa: BLE001
            self._notify(f"套用 DST 失敗：{ex}", "error")

    def _on_shard_dst_revert(self, e):
        """還原 DST 到原始值（分類分片）"""
        if not self.shard_detail_selected_key:
            show_snack(self.page, "請先選擇 key", C.GOLD)
            return

        self.shard_dst_field.value = str(self.shard_dst_original or "")
        show_snack(self.page, "已還原到原始值", C.DIA)
        self._refresh_disabled_state()
        if self.page:
            self.page.update()

    def _on_shard_dst_restore_latest(self, e):
        """還原最新歷史紀錄（不立即寫入快取）"""
        if self.ui_busy:
            self._notify("目前忙碌中，暫停還原", "warn")
            return

        ctype = str(self.shard_detail_selected_type or "")
        key = str(self.shard_detail_selected_key or "")
        if not ctype or not key:
            self._notify("請先選擇分片與 key", "warn")
            return

        # 載入最新歷史紀錄
        records = self._history_load_recent(ctype, key, limit=1)
        if not records:
            self._notify("此 key 目前沒有歷史紀錄", "warn")
            return

        latest = records[0]
        old_dst = str(latest.get("old_dst", ""))

        # 只填入 DST 輸入框，不寫入快取
        self.shard_dst_field.value = old_dst
        self._notify(
            "已載入最新歷史紀錄到 DST（尚未寫入快取，請點「套用 DST」儲存）", "info"
        )
        if self.page:
            self.page.update()
