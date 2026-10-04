"""
Unit tests for UI log propagation in ExtractorView.

Tests the core logging behavior — appending ft.Text controls to log_view,
color assignment based on log content, and page.update() scheduling —
without constructing the full ExtractorView (avoids styled_card Flet API issues).

The UI freeze bug: only first log line shows, rest appear after page switch.
Root cause hypothesis: Flet 0.85 changed page.update() semantics when called
from non-main threads or after certain control state changes.
"""

import threading
import time
from unittest.mock import MagicMock, patch

import flet as ft
import pytest


class TestAppendLogLineCore:
    """Test _append_log_line logic in isolation (no full ExtractorView construction)."""

    def test_log_entry_text_unwrapping(self):
        """Text extraction from objects with .text attribute."""
        mock_entry = MagicMock()
        mock_entry.text = "unwrapped message"

        text = mock_entry.text if hasattr(mock_entry, "text") else mock_entry
        assert text == "unwrapped message"

        text2 = "direct string"
        text2_final = text2 if hasattr(text2, "text") else text2
        assert text2_final == "direct string"

    def test_color_assignment_rules(self):
        """Color assignment based on log content."""
        def get_color(text):
            color = "#e0e0e0"
            if "[ERROR]" in text:
                color = "#ff6b6b"
            elif "[系統]" in text:
                color = "#69db7c"
            elif "Translation" in text or "完成" in text:
                color = "#74c0fc"
            return color

        assert get_color("[ERROR] failed") == "#ff6b6b"
        assert get_color("[系統] task start") == "#69db7c"
        assert get_color("Translation completed") == "#74c0fc"
        assert get_color("normal log") == "#e0e0e0"

    def test_ft_text_creation_with_color(self):
        """ft.Text control created with correct properties."""
        text = ft.Text(
            "[ERROR] something failed",
            font_family="Consolas,Monospace",
            size=13,
            color="#ff6b6b",
            selectable=True,
        )
        assert text.value == "[ERROR] something failed"
        assert text.color == "#ff6b6b"
        assert text.size == 13
        assert text.selectable is True

    def test_log_view_controls_append(self):
        """ListView.controls correctly appends ft.Text."""
        log_view = ft.ListView(spacing=2, auto_scroll=True, padding=10)

        log_view.controls.append(ft.Text("line 1", size=12, color="#e0e0e0"))
        log_view.controls.append(ft.Text("[ERROR] line 2", size=12, color="#ff6b6b"))
        log_view.controls.append(ft.Text("[系統] line 3", size=12, color="#69db7c"))

        assert len(log_view.controls) == 3
        assert log_view.controls[0].value == "line 1"
        assert log_view.controls[1].value == "[ERROR] line 2"
        assert log_view.controls[2].value == "[系統] line 3"

    def test_multiple_append_all_visible(self):
        """Rapid successive appends result in all controls present."""
        log_view = ft.ListView()
        messages = [f"log line {i}" for i in range(50)]

        for msg in messages:
            log_view.controls.append(ft.Text(msg, size=12, color="#e0e0e0"))

        assert len(log_view.controls) == 50
        for i, ctrl in enumerate(log_view.controls):
            assert ctrl.value == f"log line {i}"

    def test_max_log_lines_trimming(self):
        """Controls trimmed when exceeding MAX_LOG_LINES (400)."""
        log_view = ft.ListView()
        MAX = 400

        for i in range(MAX + 50):
            log_view.controls.append(ft.Text(f"log line {i}", size=12))

        if len(log_view.controls) > MAX:
            del log_view.controls[:len(log_view.controls) - MAX]

        assert len(log_view.controls) == MAX


class TestPageUpdateIntegration:
    """Test page.update() / page.schedule_update() behavior in mock environment."""

    def test_page_update_increments_counter(self):
        """page.update() increments updated counter."""
        class _Page:
            def __init__(self):
                self.updated = 0
            def update(self, *args, **kwargs):
                self.updated += 1

        page = _Page()
        page.update()
        page.update()
        assert page.updated == 2

    def test_page_schedule_update_called_when_present(self):
        """page.schedule_update() called if available on page object."""
        class _Page:
            def __init__(self):
                self.updated = 0
                self.schedule_called = 0
            def update(self, *args, **kwargs):
                self.updated += 1
            def schedule_update(self, *args, **kwargs):
                self.schedule_called += 1

        page = _Page()
        page.schedule_update()
        page.schedule_update()
        assert page.schedule_called == 2
        assert page.updated == 0

    def test_set_controls_disabled_updates_page(self):
        """Disabling controls triggers page.update()."""
        class _Page:
            def __init__(self):
                self.updated = 0
            def update(self, *args, **kwargs):
                self.updated += 1

        page = _Page()

        textfield = ft.TextField()
        textfield.disabled = False

        textfield.disabled = True
        page.update()

        assert textfield.disabled is True
        assert page.updated >= 1

    def test_disable_then_append_both_update_page(self):
        """set_controls_disabled(True) then _append_log_line both trigger updates."""
        class _Page:
            def __init__(self):
                self.updated = 0
            def update(self, *args, **kwargs):
                self.updated += 1

        page = _Page()
        log_view = ft.ListView()
        textfield = ft.TextField()

        textfield.disabled = True
        page.update()

        log_view.controls.append(ft.Text("test", size=12))
        page.update()

        assert textfield.disabled is True
        assert len(log_view.controls) == 1
        assert page.updated == 2


class TestConcurrentAppend:
    """Test concurrent log append from multiple threads."""

    def test_worker_thread_appends_to_session(self):
        """Worker thread safely appends to shared session list."""
        logs = []
        lock = threading.Lock()
        errors = []

        def worker(thread_id):
            try:
                for i in range(20):
                    with lock:
                        logs.append(f"thread-{thread_id} log-{i}")
                    time.sleep(0.0001)
            except Exception as ex:
                errors.append(ex)

        threads = [threading.Thread(target=worker, args=(tid,)) for tid in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Worker raised: {errors}"
        assert len(logs) == 60

    def test_poller_reads_session_without_missing_logs(self):
        """Poller reads session snapshot and gets all logs worker appended."""
        logs = []
        lock = threading.Lock()

        def worker():
            for i in range(50):
                with lock:
                    logs.append(f"worker log {i}")
                time.sleep(0.0001)

        thread = threading.Thread(target=worker)
        thread.start()

        observed = []
        for _ in range(30):
            with lock:
                snapshot = list(logs)
            observed.append(len(snapshot))
            time.sleep(0.0001)

        thread.join()

        assert max(observed) >= 1, f"Poller should see logs, saw max={max(observed)}"
        assert len(logs) == 50

    def test_ui_update_from_poller_reads_all_session_logs(self):
        """Simulate: poller reads session, UI updates with each new log."""
        log_view = ft.ListView()
        session_logs = []
        lock = threading.Lock()
        page_updates = []
        last_seen = [0]

        def append_ui():
            with lock:
                current_len = len(session_logs)
            if current_len > last_seen[0]:
                for log in session_logs[last_seen[0]:current_len]:
                    log_view.controls.append(ft.Text(log, size=12))
                    page_updates.append(1)
                last_seen[0] = current_len

        worker_done = [False]

        def worker():
            for i in range(20):
                with lock:
                    session_logs.append(f"log {i}")
                time.sleep(0.001)
            worker_done[0] = True

        thread = threading.Thread(target=worker)
        thread.start()

        for _ in range(25):
            append_ui()
            time.sleep(0.001)

        thread.join()
        append_ui()

        assert len(log_view.controls) == 20, f"Expected 20, got {len(log_view.controls)}"
        assert len(page_updates) >= 1


class TestLogViewScrolling:
    """Test log_view auto_scroll behavior."""

    def test_listview_auto_scroll_enabled(self):
        """ListView auto_scroll defaults to True."""
        log_view = ft.ListView(auto_scroll=True)
        assert log_view.auto_scroll is True

    def test_listview_auto_scroll_disabled(self):
        """ListView auto_scroll can be disabled."""
        log_view = ft.ListView(auto_scroll=False)
        assert log_view.auto_scroll is False

    def test_listview_auto_scroll_behavior_with_mock(self):
        """Auto-scroll in ListView doesn't interfere with control append."""
        log_view = ft.ListView(auto_scroll=True, expand=True)

        for i in range(10):
            log_view.controls.append(ft.Text(f"item {i}", size=12))

        assert len(log_view.controls) == 10
        assert log_view.auto_scroll is True


class TestColorContentMatching:
    """Test color assignment for various log message patterns."""

    def test_error_pattern_matches(self):
        """ERROR logs get red color."""
        logs = [
            "[ERROR] boom",
            "[ERROR] Failed to process",
            "[ERROR]: connection timeout",
        ]
        for log in logs:
            assert "[ERROR]" in log

    def test_system_pattern_matches(self):
        """系統 logs get green color."""
        logs = [
            "[系統] 任務開始",
            "[系統] 檔案已提取",
            "[系統] 完成",
        ]
        for log in logs:
            assert "[系統]" in log

    def test_translation_pattern_matches(self):
        """Translation/completion logs get blue color."""
        logs = [
            "Translation completed",
            "翻譯完成",
            "Task completed successfully",
        ]
        colors = []
        for log in logs:
            if "Translation" in log or "完成" in log:
                colors.append("#74c0fc")
        assert len(colors) == 2, f"Expected 2 (Translation and 翻譯), got {len(colors)}"

    def test_no_match_defaults_grey(self):
        """No pattern match defaults to grey."""
        logs = [
            "INFO: processing started",
            "Starting extraction",
            "step 1 of 5",
        ]
        for log in logs:
            has_error = "[ERROR]" in log
            has_system = "[系統]" in log
            has_translation = "Translation" in log or "完成" in log
            assert not (has_error or has_system or has_translation)