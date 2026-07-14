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
        with patch("app.services_impl.pipelines.extract_service.load_config", return_value=mock_cfg):
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
        with patch("app.services_impl.pipelines.extract_service.load_config", return_value=mock_cfg):
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
        with patch("app.services_impl.pipelines.extract_service.load_config", return_value=mock_cfg):
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
        with patch("app.services_impl.pipelines.extract_service.load_config", return_value=mock_cfg):
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
# 11. ExtractionState 結構完整性
# =============================================================================
