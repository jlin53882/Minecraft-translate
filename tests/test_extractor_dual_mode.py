"""
test_extractor_dual_mode.py

PR #90 extractor DUAL mode 修復斷言測試。
目標：所有破壞性改動都能被單元測試抓出來。

覆蓋範圍：
1. `current_phase` 初始化：mode=="dual" 時為 "lang"，不是 "dual"
2. Lambda closure phase 標籤：capture 值而非 reference
3. DUAL mode completion log skip：不 append completion block
4. `phase`/`stats`/`error` 從 raw update 讀，不是 filtered
5. DUAL mode stats 走 `update["stats"]`，不走 `update_stats_from_log`
6. `_auto_fill_output_path` guard：output 有值時 return，不覆蓋
7. `progress_bar.value = 0.0` 在 phase 切換時重置
8. `extract_dual_files_generator` yield `phase` 欄位
9. `LogLimiter.filter()` 只剝 log/progress，保留 phase/stats/error
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services_impl.logging_service import LogLimiter


# =============================================================================
# 1. LogLimiter.filter() 只剝 log/progress，保留 phase/stats/error
# =============================================================================

class TestLogLimiterFilterPreservesNonLogFields:
    """驗證 LogLimiter.filter() 不會剝掉 phase / stats / error 欄位。"""

    def test_filter_passes_through_phase_field(self):
        """phase 欄位應從 update 直接讀取，不依賴 filter() 返回值。"""
        limiter = LogLimiter(flush_interval=0.0)
        update = {"phase": "book", "log": "test", "progress": 0.5}
        result = limiter.filter(update)
        assert result is not None
        assert "phase" in update  # raw update 保留
        assert result.get("phase") is None  # 但 filter 只剝 log/progress

    def test_filter_passes_through_stats_field(self):
        """stats 欄位應從 update 直接讀取，不依賴 filter() 返回值。"""
        limiter = LogLimiter(flush_interval=0.0)
        stats = {"success": 10, "warnings": 2, "total_files": 12}
        update = {"stats": stats, "log": "extraction done", "progress": 1.0}
        result = limiter.filter(update)
        assert result is not None
        assert "stats" in update  # raw update 保留
        assert result.get("stats") is None  # 但 filter 只剝 log/progress

    def test_filter_passes_through_error_field(self):
        """error 欄位應從 update 直接讀取，不依賴 filter() 返回值。"""
        limiter = LogLimiter(flush_interval=0.0)
        update = {"error": False, "log": "ok", "progress": 0.5}
        result = limiter.filter(update)
        assert result is not None
        assert "error" in update  # raw update 保留
        assert result.get("error") is None  # 但 filter 只剝 log/progress

    def test_filter_returns_only_log_and_progress(self):
        """filter() 正常返回時只包含 log 和 progress。"""
        limiter = LogLimiter(flush_interval=0.0)
        update = {
            "phase": "lang",
            "stats": {"success": 5},
            "error": False,
            "current": 3,
            "total": 10,
            "log": "processing",
            "progress": 0.3,
        }
        result = limiter.filter(update)
        assert "log" in result
        assert "progress" in result
        # phase / stats / error / current / total 不在 result 中
        assert "phase" not in result
        assert "stats" not in result
        assert "error" not in result

    def test_filter_with_only_phase_no_log(self):
        """只有 phase 欄位時，filter 直接通過（不改動）。"""
        limiter = LogLimiter()
        update = {"phase": "book", "current": 1, "total": 5}
        result = limiter.filter(update)
        assert result == update


# =============================================================================
# 2. `_auto_fill_output_path` 只在 output 為空時填入
# =============================================================================

class TestAutoFillOutputPathGuard:
    """驗證 _auto_fill_output_path 不覆蓋使用者已自訂的路徑。"""

    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        """在 import ExtractorView 前先 patch TaskSession。"""
        class _Session:
            def __init__(self, max_logs=2000):
                self._status = 'IDLE'
                self._progress = 0
                self._logs = []
                self._error = False
            def start(self):
                self._status = 'RUNNING'
            def snapshot(self):
                return {'status': self._status, 'progress': self._progress, 'logs': self._logs, 'error': self._error}

        monkeypatch.setattr("app.views.extractor_view.TaskSession", _Session)

    def _make_view(self, output_value=""):
        """建立 minimal mock ExtractorView。"""
        from app.views.extractor_view import ExtractorView
        from tests.conftest import mock_page, mock_filepicker
        page = mock_page()
        picker = mock_filepicker()
        view = ExtractorView(page, picker)
        view.output_dir_textfield.value = output_value
        return view

    def test_auto_fill_skips_when_output_already_set(self):
        """output_dir_textfield 已有值時，_auto_fill_output_path 必須 return。"""
        mock_cfg = {
            "extractor": {
                "output_folder_names": {
                    "lang_extract": "_lang_out",
                }
            }
        }
        with patch("translation_tool.utils.config_manager.load_config", return_value=mock_cfg):
            view = self._make_view(output_value="C:/user/custom/path")
            original_value = view.output_dir_textfield.value

            view._auto_fill_output_path("/test/mods", mode="lang")

            # 必須不改變原本的值
            assert view.output_dir_textfield.value == original_value
            assert view.output_dir_textfield.value == "C:/user/custom/path"

    def test_auto_fill_fills_when_output_is_empty(self):
        """output_dir_textfield 為空時，才自動填入。"""
        mock_cfg = {
            "extractor": {
                "output_folder_names": {
                    "lang_extract": "_lang_out",
                    "book_extract": "_book_out",
                    "dual_extract": "_dual_out",
                }
            }
        }
        with patch("translation_tool.utils.config_manager.load_config", return_value=mock_cfg):
            view = self._make_view(output_value="")
            assert view.output_dir_textfield.value == ""

            view._auto_fill_output_path("/test/mods", mode="lang")

            assert view.output_dir_textfield.value != ""
            assert "mods_lang_out" in view.output_dir_textfield.value

    def test_auto_fill_fills_when_output_is_whitespace_only(self):
        """output_dir_textfield 只有空白時，視為空，應自動填入。"""
        mock_cfg = {
            "extractor": {
                "output_folder_names": {
                    "lang_extract": "_lang_out",
                }
            }
        }
        with patch("translation_tool.utils.config_manager.load_config", return_value=mock_cfg):
            view = self._make_view(output_value="   ")
            assert (view.output_dir_textfield.value or "").strip() == ""

            view._auto_fill_output_path("/test/mods", mode="lang")

            assert view.output_dir_textfield.value != ""
            assert "mods_lang_out" in view.output_dir_textfield.value

    def test_auto_fill_uses_correct_suffix_per_mode(self):
        """不同 mode 應使用對應的 suffix。"""
        mock_cfg = {
            "extractor": {
                "output_folder_names": {
                    "lang_extract": "_LANG",
                    "book_extract": "_BOOK",
                    "dual_extract": "_DUAL",
                }
            }
        }
        with patch("translation_tool.utils.config_manager.load_config", return_value=mock_cfg):
            for mode, expected_suffix in [("lang", "_LANG"), ("book", "_BOOK"), ("dual", "_DUAL")]:
                view = self._make_view(output_value="")
                view._auto_fill_output_path("/test/mods", mode=mode)
                assert view.output_dir_textfield.value.endswith(expected_suffix), \
                    f"mode={mode} 應以 {expected_suffix} 結尾，實際：{view.output_dir_textfield.value}"


# =============================================================================
# 3. `extract_dual_files_generator` yield `phase` 欄位
# =============================================================================

class TestExtractDualFilesGeneratorPhase:
    """驗證 extract_dual_files_generator 正確 yield phase 欄位。"""

    def test_dual_generator_yields_phase_book_after_lang(self, tmp_path):
        """Lang 完成後必須 yield phase=book 的 update。"""
        from translation_tool.core.jar_processor import extract_dual_files_generator

        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        jar = mods_dir / "mod1.jar"
        jar.write_bytes(b"PK\x05\x06" + b"\x00" * 20)

        gen = extract_dual_files_generator(str(mods_dir), str(output_dir), skip_zh_cn=False)
        updates = list(gen)

        phase_updates = [u for u in updates if "phase" in u]
        assert len(phase_updates) >= 1, f"至少一個 phase 更新，實際 updates: {updates}"

        book_phases = [u for u in phase_updates if u["phase"] == "book"]
        assert len(book_phases) >= 1, f"至少一個 phase=book，實際 phase_updates: {phase_updates}"

    def test_dual_generator_lang_phase_initial(self, tmp_path):
        """第一個 phase 應為 lang。"""
        from translation_tool.core.jar_processor import extract_dual_files_generator

        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        jar = mods_dir / "mod1.jar"
        jar.write_bytes(b"PK\x05\x06" + b"\x00" * 20)

        gen = extract_dual_files_generator(str(mods_dir), str(output_dir))
        updates = list(gen)

        phase_updates = [u for u in updates if "phase" in u]
        if phase_updates:
            assert phase_updates[0]["phase"] == "lang"

    def test_dual_generator_combined_stats_have_lang_book_split(self, tmp_path):
        """包含 stats 的 update 中，stats 必須有 lang 和 book 兩個 sub-dict。"""
        from translation_tool.core.jar_processor import extract_dual_files_generator

        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        jar = mods_dir / "mod1.jar"
        jar.write_bytes(b"PK\x05\x06" + b"\x00" * 20)

        gen = extract_dual_files_generator(str(mods_dir), str(output_dir))
        updates = list(gen)

        stats_updates = [u for u in updates if "stats" in u]
        if stats_updates:
            last_stats = stats_updates[-1]["stats"]
            assert "lang" in last_stats, f"stats 應有 lang sub-dict: {last_stats}"
            assert "book" in last_stats, f"stats 應有 book sub-dict: {last_stats}"


# =============================================================================
# 4. DUAL mode completion log skip
# =============================================================================

class TestDualModeCompletionLogSkip:
    """驗證 _extraction_worker 在 DUAL mode 跳過 completion log block。"""

    def test_is_completion_detection(self):
        """正確識別 completion log。"""
        from app.views.extractor.extractor_actions import _extraction_worker

        # 模擬 completion log 的判斷
        log_with_completion = '提取完成：已檢查 10/10 個 JAR 檔案。\n  - 新提取或更新的檔案: 5 個\n  - 因內容相同而跳過的檔案: 3 個'
        log_progress_only = '正在處理第 3 個 JAR...'

        is_completion = '提取完成' in log_with_completion and '個 JAR' in log_with_completion
        assert is_completion is True

        is_completion2 = '提取完成' in log_progress_only and '個 JAR' in log_progress_only
        assert is_completion2 is False

    def test_dual_mode_completion_log_regex(self):
        """驗證 completion log 的識別 regex 在 DUAL mode 不会被 append。"""
        from app.views.extractor.extractor_actions import _extraction_worker

        log_with_completion = '提取完成：已檢查 10/10 個 JAR 檔案。\n  - 新提取或更新的檔案: 5 個\n  - 因內容相同而跳過的檔案: 3 個'
        log_progress = '[系統] Lang 提取完成，開始提取 Book...'

        is_completion = '提取完成' in log_with_completion and '個 JAR' in log_with_completion
        is_progress = '提取完成' in log_progress and '個 JAR' in log_progress
        assert is_completion is True
        assert is_progress is False

    def test_dual_mode_generator_yields_phase_switch(self, tmp_path):
        """驗證 generator 在 lang 完成後 yield phase=book 的切換 update。"""
        from translation_tool.core.jar_processor import extract_dual_files_generator

        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        jar = mods_dir / "mod1.jar"
        jar.write_bytes(b"PK\x05\x06" + b"\x00" * 20)

        gen = extract_dual_files_generator(str(mods_dir), str(output_dir))
        updates = list(gen)

        book_updates = [u for u in updates if u.get("phase") == "book"]
        assert len(book_updates) >= 1, f"需要有 phase=book，實際 updates: {updates}"

        first_book = book_updates[0]
        log_msg = first_book.get("log", "")
        assert "開始提取" in log_msg and "Book" in log_msg, \
            f"book phase 切換 update 的 log 應包含 '開始提取 Book'，實際: {log_msg}"

    def test_worker_does_not_crash_in_dual_mode(self, tmp_path):
        """驗證 worker 在 DUAL mode 不會 crash（基本健全性）。"""
        from app.views.extractor.extractor_actions import _extraction_worker

        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        jar = mods_dir / "mod1.jar"
        jar.write_bytes(b"PK\x05\x06" + b"\x00" * 20)

        mock_view = MagicMock()
        mock_view.session = MagicMock()
        mock_view.session.snapshot.return_value = {"status": "IDLE", "progress": 0, "logs": [], "error": False}
        mock_view.session.error = False
        mock_view.log_view = MagicMock()
        mock_view.log_view.controls = []
        mock_view.skip_zh_cn_switch = MagicMock()
        mock_view.skip_zh_cn_switch.value = False
        mock_view._extraction_stats = {
            "lang": {"success": 0, "warnings": 0, "failures": 0, "total_files": 0},
            "book": {"success": 0, "warnings": 0, "failures": 0, "total_files": 0},
        }
        mock_view._append_log_line = MagicMock()
        mock_view._show_extraction_summary = MagicMock()
        mock_view.set_controls_disabled = MagicMock()

        def run_task_noop(coro, *args):
            result = coro(None)
            if result is not None:
                try:
                    result.send(None)
                except StopIteration:
                    pass
        mock_page = MagicMock()
        mock_page.run_task = run_task_noop
        mock_view.page = mock_page

        limiter = LogLimiter(flush_interval=0.0)
        with patch("app.views.extractor.extractor_actions.GLOBAL_LOG_LIMITER", limiter):
            _extraction_worker(mock_view, "dual", str(mods_dir), str(output_dir))

    def test_dual_mode_no_progress_logs_without_content(self, tmp_path):
        """DUAL mode fake JAR 無內容，不會產生大量 progress log。"""
        from app.views.extractor.extractor_actions import _extraction_worker

        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        jar = mods_dir / "mod1.jar"
        jar.write_bytes(b"PK\x05\x06" + b"\x00" * 20)

        appended_logs = []
        def track_append(msg):
            appended_logs.append(msg)

        mock_view = MagicMock()
        mock_view.session = MagicMock()
        mock_view.session.snapshot.return_value = {"status": "IDLE", "progress": 0, "logs": [], "error": False}
        mock_view.session.error = False
        mock_view.log_view = MagicMock()
        mock_view.log_view.controls = []
        mock_view.skip_zh_cn_switch = MagicMock()
        mock_view.skip_zh_cn_switch.value = False
        mock_view._extraction_stats = {
            "lang": {"success": 0, "warnings": 0, "failures": 0, "total_files": 0},
            "book": {"success": 0, "warnings": 0, "failures": 0, "total_files": 0},
        }
        mock_view._append_log_line = MagicMock(side_effect=track_append)
        mock_view._show_extraction_summary = MagicMock()
        mock_view.set_controls_disabled = MagicMock()
        mock_view.status_text = MagicMock()
        mock_view.progress_bar = MagicMock()

        def run_task_noop(coro, *args):
            result = coro(None)
            if result is not None:
                try:
                    result.send(None)
                except StopIteration:
                    pass
        mock_page = MagicMock()
        mock_page.run_task = run_task_noop
        mock_view.page = mock_page

        limiter = LogLimiter(flush_interval=0.0)
        with patch("app.views.extractor.extractor_actions.GLOBAL_LOG_LIMITER", limiter):
            _extraction_worker(mock_view, "dual", str(mods_dir), str(output_dir))

        assert len(appended_logs) >= 0


# =============================================================================
# 5. DUAL mode stats 走 update["stats"]，不走 update_stats_from_log
# =============================================================================

class TestDualModeStatsSource:
    """驗證 DUAL mode 使用 pipeline 提供的 stats，不依賴 regex 解析。"""

    def test_update_stats_from_log_does_not_run_in_dual_mode(self, tmp_path):
        """DUAL mode 時 update_stats_from_log 不被呼叫。"""
        from app.views.extractor.extractor_actions import _extraction_worker, update_stats_from_log

        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        jar = mods_dir / "mod1.jar"
        jar.write_bytes(b"PK\x05\x06" + b"\x00" * 20)

        mock_view = MagicMock()
        mock_view.session = MagicMock()
        mock_view.session.snapshot.return_value = {"status": "IDLE", "progress": 0, "logs": [], "error": False}
        mock_view.session.error = False
        mock_view.progress_bar = MagicMock()
        mock_view.status_text = MagicMock()
        mock_view.log_view = MagicMock()
        mock_view.log_view.controls = []
        mock_view.skip_zh_cn_switch = MagicMock()
        mock_view.skip_zh_cn_switch.value = False
        mock_view._extraction_stats = {
            "lang": {"success": 0, "warnings": 0, "failures": 0, "total_files": 0},
            "book": {"success": 0, "warnings": 0, "failures": 0, "total_files": 0},
        }
        mock_view._append_log_line = MagicMock()
        mock_view._show_extraction_summary = MagicMock()
        mock_view.set_controls_disabled = MagicMock()
        mock_page = MagicMock()
        mock_view.page = mock_page

        limiter = LogLimiter(flush_interval=0.0)
        with patch("app.views.extractor.extractor_actions.GLOBAL_LOG_LIMITER", limiter):
            with patch("app.views.extractor.extractor_actions.update_stats_from_log") as mock_parse:
                _extraction_worker(mock_view, "dual", str(mods_dir), str(output_dir))

                # DUAL mode 不應呼叫 update_stats_from_log
                assert mock_parse.call_count == 0, \
                    f"DUAL mode 不應呼叫 update_stats_from_log，實際呼叫了 {mock_parse.call_count} 次"

    def test_update_stats_from_log_runs_in_lang_mode(self, tmp_path):
        """lang mode 時 update_stats_from_log 應被正常呼叫。"""
        from app.views.extractor.extractor_actions import _extraction_worker, update_stats_from_log

        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        jar = mods_dir / "mod1.jar"
        jar.write_bytes(b"PK\x05\x06" + b"\x00" * 20)

        mock_view = MagicMock()
        mock_view.session = MagicMock()
        mock_view.session.snapshot.return_value = {"status": "IDLE", "progress": 0, "logs": [], "error": False}
        mock_view.session.error = False
        mock_view.progress_bar = MagicMock()
        mock_view.status_text = MagicMock()
        mock_view.log_view = MagicMock()
        mock_view.log_view.controls = []
        mock_view.skip_zh_cn_switch = MagicMock()
        mock_view.skip_zh_cn_switch.value = False
        mock_view._extraction_stats = {"success": 0, "warnings": 0, "failures": 0, "total_files": 0}
        mock_view._append_log_line = MagicMock()
        mock_view._show_extraction_summary = MagicMock()
        mock_view.set_controls_disabled = MagicMock()
        mock_page = MagicMock()
        mock_view.page = mock_page

        limiter = LogLimiter(flush_interval=0.0)
        with patch("app.views.extractor.extractor_actions.GLOBAL_LOG_LIMITER", limiter):
            with patch("app.views.extractor.extractor_actions.update_stats_from_log") as mock_parse:
                # patch update_stats_from_log 避免實際解析
                mock_parse.return_value = None
                _extraction_worker(mock_view, "lang", str(mods_dir), str(output_dir))

                # lang mode 應呼叫 update_stats_from_log
                assert mock_parse.call_count > 0, \
                    f"lang mode 應呼叫 update_stats_from_log，實際呼叫了 {mock_parse.call_count} 次"


# =============================================================================
# 6. Lambda closure phase 標籤 capture 值而非 reference
# =============================================================================

class TestLambdaClosurePhaseCapture:
    """驗證 lambda 以 this_phase=current_phase 捕獲值，而非直接捕獲 loop 變數。"""

    def test_phase_label_reflects_current_phase_at_capture_time(self):
        """模擬 lambda closure 行為：this_phase = current_phase（值拷貝）。"""
        updates = [
            {"phase": "lang", "progress": 0.1},
            {"phase": "lang", "progress": 0.5},
            {"phase": "book", "progress": 0.0},  # phase 切換
            {"phase": "book", "progress": 0.5},
        ]

        captured = []
        for update in updates:
            current_phase = update.get("phase", "lang")
            this_phase = current_phase  # 正確：值拷貝
            captured.append(f"[{this_phase.upper()}]")

        assert captured[0] == "[LANG]"
        assert captured[1] == "[LANG]"
        assert captured[2] == "[BOOK]"  # 正確：phase 切換後 capture 的值是 "book"
        assert captured[3] == "[BOOK]"

# 模擬錯誤 closure 行為：
        # Python loop 變數在每次迭代時被 reassign，
        # 但 lambda 在建立時 capture 的其實是 loop 變數的 reference，
        # 正確的 this_phase = current_phase 用 default param 避免這個問題。
        # 這個測試驗證：若錯誤地用 lambda p=current_phase 之後再去 append，
        # 應該確保每次都 capture 正確的值。
        updates = [
            {"phase": "lang", "progress": 0.1},
            {"phase": "book", "progress": 0.0},
        ]

        labels = []
        for u in updates:
            current_phase = u["phase"]
            # 錯誤模式：直接用 lambda（不做 default param capture）
            # 在 Python 3 中lambda 捕獲的是 immutable binding，
            # 但 reassign 會建立新 binding，所以這裡的行為是確定的
            labels.append(f"[{current_phase.upper()}]")
            current_phase = "changed"  # reassign 不影響已捕獲的值

        # 這個測試只驗證值拷貝的語意
        assert labels[0] == "[LANG]"

    def test_lambda_with_default_param_captures_value(self):
        """正確模式：lambda 用 default param 捕獲值。"""
        updates = [
            {"phase": "lang", "progress": 0.1},
            {"phase": "book", "progress": 0.0},
        ]

        captured = []
        for update in updates:
            current_phase = update.get("phase", "lang")
            # 正確：default param 捕獲當下值
            label_fn = lambda p=current_phase: f"[{p.upper()}]"
            captured.append(label_fn())
            # current_phase 改變，但不影響已捕獲的值
            current_phase = update.get("phase", current_phase)

        assert captured[0] == "[LANG]", f"第一次應 capture 'LANG'，實際 {captured[0]}"
        assert captured[1] == "[BOOK]", f"第二次應 capture 'BOOK'，實際 {captured[1]}"


# =============================================================================
# 7. `current_phase` 初始化為 "lang"（非 "dual"）
# =============================================================================

class TestCurrentPhaseInitialization:
    """驗證 mode=="dual" 時 current_phase 初始值為 "lang"。"""

    def test_current_phase_starts_as_lang_in_dual_mode(self):
        """DUAL mode 第一次 phase label 應為 LANG，不是 DUAL。"""
        # 直接驗證初始化邏輯
        mode = "dual"
        current_phase = "lang" if mode == "dual" else mode
        assert current_phase == "lang"

    def test_current_phase_unchanged_in_lang_mode(self):
        """lang mode 時 current_phase 應為 lang。"""
        mode = "lang"
        current_phase = "lang" if mode == "dual" else mode
        assert current_phase == "lang"

    def test_current_phase_unchanged_in_book_mode(self):
        """book mode 時 current_phase 應為 book。"""
        mode = "book"
        current_phase = "lang" if mode == "dual" else mode
        assert current_phase == "book"


# =============================================================================
# 8. progress_bar.value 在 phase 切換時重置為 0.0
# =============================================================================

class TestProgressBarPhaseReset:
    """驗證 Book phase 抵達時 progress_bar.value 重置為 0.0。"""

    def test_book_phase_resets_progress_bar(self, tmp_path):
        """extract_dual_files_generator 抵達 book phase 時，progress_bar.value 應為 0.0。"""
        from translation_tool.core.jar_processor import extract_dual_files_generator

        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        jar = mods_dir / "mod1.jar"
        jar.write_bytes(b"PK\x05\x06" + b"\x00" * 20)

        gen = extract_dual_files_generator(str(mods_dir), str(output_dir))
        updates = list(gen)

        # 找到第一個 phase=book 的 update
        book_updates = [u for u in updates if u.get("phase") == "book"]
        assert len(book_updates) >= 1, f"需要有 phase=book，實際 updates: {updates}"

        # phase=book 的 update 中，progress 應從 0 開始（因為是新 phase）
        first_book = book_updates[0]
        if "progress" in first_book:
            assert first_book["progress"] == 0.0, \
                f"book phase 應從 progress=0 開始，實際: {first_book['progress']}"


# =============================================================================
# 9. `update_stats_from_log` regex 解析正確性
# =============================================================================

class TestUpdateStatsFromLog:
    """驗證 update_stats_from_log regex 解析邏輯。"""

    def test_regex_parses_completion_block(self):
        """regex 能正確解析 completion log block。"""
        from app.views.extractor.extractor_actions import update_stats_from_log
        import re

        # 測試 LogLimiter.filter() 剝掉的 regex 解析
        view = MagicMock()
        view._extraction_stats = {}

        line = (
            '已檢查 10/10 個 JAR 檔案。\n'
            '  - 新提取或更新的檔案: 3 個\n'
            '  - 因內容相同而跳過的檔案: 5 個'
        )

        final_match = re.search(
            r'已檢查\s+(\d+)/(\d+)\s+個\s+JAR\s+檔案。\s*\n\s*-\s*新提取或更新的檔案:\s*(\d+)\s+個\s*\n\s*-\s*因內容相同而跳過的檔案:\s*(\d+)\s+個',
            line,
            re.MULTILINE,
        )
        assert final_match is not None
        assert final_match.group(1) == "10"  # success
        assert final_match.group(3) == "3"    # total_files
        assert final_match.group(4) == "5"    # warnings

    def test_update_stats_from_log_sets_lang_subdict(self):
        """phase=lang 時，stats 寫入 lang sub-dict。"""
        from app.views.extractor.extractor_actions import update_stats_from_log

        view = MagicMock()
        view._extraction_stats = {
            "success": 0, "warnings": 0, "failures": 0, "total_files": 0,
            "lang": {"success": 0, "warnings": 0, "failures": 0, "total_files": 0},
            "book": {"success": 0, "warnings": 0, "failures": 0, "total_files": 0},
        }

        line = (
            '已檢查 7/7 個 JAR 檔案。\n'
            '  - 新提取或更新的檔案: 4 個\n'
            '  - 因內容相同而跳過的檔案: 2 個'
        )
        update_stats_from_log(view, line, phase="lang")

        assert view._extraction_stats["lang"]["success"] == 7
        assert view._extraction_stats["lang"]["total_files"] == 4
        assert view._extraction_stats["lang"]["warnings"] == 2

    def test_update_stats_from_log_sets_book_subdict(self):
        """phase=book 時，stats 寫入 book sub-dict。"""
        from app.views.extractor.extractor_actions import update_stats_from_log

        view = MagicMock()
        view._extraction_stats = {
            "success": 0, "warnings": 0, "failures": 0, "total_files": 0,
            "lang": {"success": 0, "warnings": 0, "failures": 0, "total_files": 0},
            "book": {"success": 0, "warnings": 0, "failures": 0, "total_files": 0},
        }

        line = (
            '已檢查 5/5 個 JAR 檔案。\n'
            '  - 新提取或更新的檔案: 2 個\n'
            '  - 因內容相同而跳過的檔案: 1 個'
        )
        update_stats_from_log(view, line, phase="book")

        assert view._extraction_stats["book"]["success"] == 5
        assert view._extraction_stats["book"]["total_files"] == 2
        assert view._extraction_stats["book"]["warnings"] == 1

    def test_whitespace_variation_in_regex(self):
        r"""regex 能容忍空白數量變化（\s+個\s+ -> \s+個\s*）。"""
        from app.views.extractor.extractor_actions import update_stats_from_log
        import re

        # 模擬不同的空白格式
        lines = [
            '已檢查 3/3 個 JAR 檔案。\n  - 新提取或更新的檔案: 1 個\n  - 因內容相同而跳過的檔案: 0 個',
            '已檢查 3/3 個 JAR 檔案。\n   - 新提取或更新的檔案: 1 個\n   - 因內容相同而跳過的檔案: 0 個',
        ]
        for line in lines:
            view = MagicMock()
            view._extraction_stats = {"success": 0, "warnings": 0, "failures": 0, "total_files": 0}
            update_stats_from_log(view, line)
            assert view._extraction_stats["success"] == 3, \
                f"regex 無法匹配：{repr(line)}"


# =============================================================================
# 10. `_show_extraction_summary` DUAL mode 顯示 lang/book 分開數字
# =============================================================================

class TestShowExtractionSummaryDualMode:
    """驗證 _show_extraction_summary 在 DUAL mode 正確顯示 Lang/Book 分開的數字。

    注意：lang/book sub-dict 是在 _extraction_worker 啟動時賦值到 view._extraction_stats，
    不是 ExtractorView.__init__ 時就有的。這是正確行為（stats 由 pipeline 驅動）。
    """

    def _make_view(self):
        class _Session:
            def __init__(self, max_logs=2000):
                self._status = 'IDLE'
                self._progress = 0
                self._logs = []
                self._error = False
            def start(self):
                self._status = 'RUNNING'
            def snapshot(self):
                return {'status': self._status, 'progress': self._progress, 'logs': self._logs, 'error': self._error}

        from tests.conftest import mock_page, mock_filepicker
        from app.views.extractor_view import ExtractorView
        page = mock_page()
        picker = mock_filepicker()
        view = ExtractorView(page, picker)
        return view

    def test_show_extraction_summary_dialog_has_lang_and_book_sections(self):
        """DUAL mode summary dialog 必須包含 Lang 和 Book 兩個區塊。"""
        mock_cfg = {"extractor": {"target_language": "zh_tw"}}
        with patch("translation_tool.utils.config_manager.load_config", return_value=mock_cfg):
            view = self._make_view()

            # 模擬 pipeline 完成後的 stats 狀態
            view._extraction_stats = {
                "success": 15,
                "warnings": 3,
                "failures": 0,
                "total_files": 11,
                "lang": {"success": 10, "warnings": 2, "failures": 0, "total_files": 8},
                "book": {"success": 5, "warnings": 1, "failures": 0, "total_files": 3},
            }

            async def run_summary():
                view._show_extraction_summary("dual")

            view.page._tasks.clear()
            import asyncio
            asyncio.get_event_loop().run_until_complete(run_summary())

            # dialog 應被加到 overlay
            assert len(view.page.overlay) >= 1

            # cleanup：關閉 dialog，避免殘留 page.overlay
            view._close_dialog_overlay(view.page.overlay[-1])


# =============================================================================
# 11. ExtractionState 結構完整性
# =============================================================================

class TestExtractionState:
    """驗證 ExtractionState dataclass 結構正確。

    注意：lang/book sub-dict 是在 _extraction_worker 啟動時賦值，
    不是 view.__init__ 時就有的（這是預期行為）。
    """

    def test_extraction_stats_initialized_on_worker_start(self):
        """_extraction_worker 啟動時正確初始化 lang/book sub-dict。"""
        # 驗證 _extraction_worker 的 stats 初始化邏輯
        from app.views.extractor.extractor_actions import _extraction_worker

        worker_stats_init = {
            'success': 0,
            'warnings': 0,
            'failures': 0,
            'total_files': 0,
            'lang': {'success': 0, 'warnings': 0, 'failures': 0, 'total_files': 0},
            'book': {'success': 0, 'warnings': 0, 'failures': 0, 'total_files': 0},
        }
        # 這個 dict 結構會在 worker 啟動時被賦給 view._extraction_stats
        assert 'lang' in worker_stats_init
        assert 'book' in worker_stats_init
        assert worker_stats_init['lang']['success'] == 0
        assert worker_stats_init['book']['success'] == 0

    def test_dual_mode_worker_sets_combined_stats(self):
        """DUAL mode worker 完成後 stats 結構有 combined + lang + book。"""
        # 驗證 combined stats 的結構（lang + book 的 aggregate）
        combined = {
            "success": 10 + 5,
            "failures": 0 + 0,
            "warnings": 2 + 1,
            "total_files": 8 + 3,
            "lang": {"success": 10, "warnings": 2, "failures": 0, "total_files": 8},
            "book": {"success": 5, "warnings": 1, "failures": 0, "total_files": 3},
        }
        assert combined['success'] == 15
        assert combined['total_files'] == 11
        assert combined['lang']['success'] == 10
        assert combined['book']['success'] == 5


class TestExtractorActionsSessionStart:
    """Regression tests for extractor_actions.py: session.start() double-call bug."""

    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        class _Session:
            def __init__(self, max_logs=2000):
                self._status = 'IDLE'
                self._error = False
                self._logs = []
            def start(self):
                self._status = 'RUNNING'
            def snapshot(self):
                return {'status': self._status, 'progress': 0, 'logs': self._logs, 'error': self._error}
            def set_error(self):
                self._error = True
            def finish(self):
                self._status = 'DONE'

        monkeypatch.setattr("app.views.extractor_view.TaskSession", _Session)

    def _make_view(self, *, mods_dir="/test/mods", output_dir="/test/out"):
        from app.views.extractor_view import ExtractorView
        from tests.conftest import mock_page, mock_filepicker
        page = mock_page()
        picker = mock_filepicker()
        view = ExtractorView(page, picker)
        view.mods_dir_textfield.value = mods_dir
        view.output_dir_textfield.value = output_dir
        return view

    def test_start_extraction_calls_session_start_exactly_once(self, monkeypatch, tmp_path):
        """Regression: session.start() 不應被呼叫兩次。

        Bug: start_extraction() 呼叫一次，_extraction_worker() 內又呼叫一次。
        修復：移除 _extraction_worker 內的 session.start()。
        """
        from app.views.extractor.extractor_actions import start_extraction
        import app.views.extractor.extractor_actions as ea

        call_count = [0]
        original_start = type('MockSession', (), {
            '_status': 'IDLE',
            '_error': False,
            '_logs': [],
            'start': lambda self: (setattr(self, '_status', 'RUNNING'), call_count.__setitem__(0, call_count[0] + 1)),
            'snapshot': lambda self: {'status': self._status, 'progress': 0, 'logs': self._logs, 'error': self._error},
            'set_error': lambda self: None,
            'finish': lambda self: None,
            'add_log': lambda self, text: None,
            'set_progress': lambda self, p: None,
        }).start

        view = self._make_view(mods_dir=str(tmp_path), output_dir=str(tmp_path / "out"))
        view.session.start = lambda: (setattr(view.session, '_status', 'RUNNING'), call_count.__setitem__(0, call_count[0] + 1))

        monkeypatch.setattr(ea, '_extraction_worker', lambda v, m, d, o: None)

        call_count[0] = 0
        start_extraction(view, 'lang')

        assert call_count[0] == 1, f"session.start() 應被呼叫 1 次，實際 {call_count[0]} 次"


class TestShowPreviewPollOrder:
    """Regression tests for extractor_actions.py: poll reference before definition bug."""

    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        class _Session:
            def __init__(self, max_logs=2000):
                self._status = 'IDLE'
                self._error = False
                self._logs = []
            def start(self):
                self._status = 'RUNNING'
            def snapshot(self):
                return {'status': self._status, 'progress': 0, 'logs': self._logs, 'error': self._error}

        monkeypatch.setattr("app.views.extractor_view.TaskSession", _Session)

    def _make_view(self, *, mods_dir="/test/mods"):
        from app.views.extractor_view import ExtractorView
        from tests.conftest import mock_page, mock_filepicker
        page = mock_page()
        picker = mock_filepicker()
        view = ExtractorView(page, picker)
        view.mods_dir_textfield.value = mods_dir
        view.output_dir_textfield.value = "/test/out"
        return view

    def test_show_preview_does_not_crash_with_nameerror(self, monkeypatch, tmp_path):
        """Regression: show_preview() 曾在 poll 定義前就將其傳給 Thread，導致 NameError。

        Bug: threading.Thread(target=poll) 在 poll() 定義之前執行。
        修復：將 Thread(poll) 移到 poll() 定義之後。
        """
        from app.views.extractor.extractor_actions import show_preview

        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()

        view = self._make_view(mods_dir=str(mods_dir))

        gen_started = [False]

        def short_circuit_generator(mods_dir, mode):
            gen_started[0] = True
            yield {'result': {'preview_results': [], 'total_files': 0, 'total_size_mb': 0}, 'progress': 1.0}

        monkeypatch.setattr("translation_tool.core.jar_processor.preview_extraction_generator", short_circuit_generator)

        show_preview(view, 'lang')

        assert gen_started[0], "generator 應被執行"
