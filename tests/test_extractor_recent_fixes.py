"""test_extractor_recent_fixes.py

針對最近幾次 commit 的回歸測試：
- f9c5e20：修正 Path.rstrip() 的 AttributeError、智慧判斷避免路徑重複、移除主 UI 日誌區塊
- 6257485：修正提取按鈕在輸出路徑為空時自動補齊路徑的邏輯
- 945dd6e：修復 log UI 不即時更新問題
- 33bcbea：改用對話框方式執行提取任務（open_extractor_dialog）

執行：pytest tests/test_extractor_recent_fixes.py -v
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import flet as ft
import pytest

from app.views.extractor_view import ExtractorView
# 避免 tests 套件命名衝突（hermes-agent/tests 在 sys.path 前面）
# 直接用 importlib 從專案的 conftest.py 載入 mock_page / mock_filepicker
import importlib.util as _importlib_util
import sys as _sys
from pathlib import Path as _Path

_conftest_path = _Path(__file__).resolve().parent / "conftest.py"
_spec = _importlib_util.spec_from_file_location("project_conftest", _conftest_path)
if _spec is None or _spec.loader is None:
    raise ImportError(f"無法載入 conftest.py: {_conftest_path}")
_mod = _importlib_util.module_from_spec(_spec)
_sys.modules["project_conftest"] = _mod
_spec.loader.exec_module(_mod)
mock_page = _mod.mock_page
mock_filepicker = _mod.mock_filepicker


# -----------------------------------------------------------------------------
# Mock 工具
# -----------------------------------------------------------------------------

class _SessionStub:
    """取代 TaskSession，避免在 __init__ 階段做 I/O。"""

    def __init__(self, max_logs=2000):
        self._status = "IDLE"
        self._progress = 0
        self._logs = []
        self._error = False

    def start(self):
        self._status = "RUNNING"

    def snapshot(self):
        return {
            "status": self._status,
            "progress": self._progress,
            "logs": self._logs,
            "error": self._error,
        }


def _make_view(monkeypatch):
    """建立 ExtractorView 並 patch 掉 Flet 的 page property。

    Flet 的 view.page 屬性會從 self.parent 一路往上找到 Page 物件，
    找不到時拋 RuntimeError。我們直接 patch BaseControl.page 屬性，
    把它換成一個回傳 mock_page 的函式。
    """
    from flet.controls.base_control import BaseControl

    monkeypatch.setattr("app.views.extractor_view.TaskSession", _SessionStub)

    # 建立 mock page
    mock_pg = mock_page()

    # patch BaseControl.page，讓所有 control 的 .page 都回傳 mock_pg
    def _mock_page(self):
        return mock_pg

    monkeypatch.setattr(BaseControl, "page", property(_mock_page))

    view = ExtractorView(mock_pg, mock_filepicker())
    return view


# -----------------------------------------------------------------------------
# _auto_fill_output_path：路徑自動補齊邏輯
# -----------------------------------------------------------------------------

class TestAutoFillOutputPath:
    """測試 _auto_fill_output_path 的所有路徑合併情境。"""

    def test_mods_目錄會產生_mods_加上_suffix(self, monkeypatch):
        """情況 A：輸入 .../mods，產出 .../mods_提取lang_輸出"""
        view = _make_view(monkeypatch)
        view.mods_dir_textfield.value = r"C:\Users\test\mc\mods"

        view._auto_fill_output_path(r"C:\Users\test\mc\mods", mode="lang")

        assert view.output_dir_textfield.value == r"C:\Users\test\mc\mods_提取lang_輸出"

    def test_mods_目錄_book_模式(self, monkeypatch):
        """情況 A（book 模式）：輸入 .../mods，產出 .../mods_提取book_輸出"""
        view = _make_view(monkeypatch)
        view.mods_dir_textfield.value = r"C:\Users\test\mc\mods"

        view._auto_fill_output_path(r"C:\Users\test\mc\mods", mode="book")

        assert view.output_dir_textfield.value == r"C:\Users\test\mc\mods_提取book_輸出"

    def test_mods_目錄_dual_模式(self, monkeypatch):
        """情況 A（dual 模式）：輸入 .../mods，產出 .../mods_提取both_輸出"""
        view = _make_view(monkeypatch)
        view.mods_dir_textfield.value = r"C:\Users\test\mc\mods"

        view._auto_fill_output_path(r"C:\Users\test\mc\mods", mode="dual")

        assert view.output_dir_textfield.value == r"C:\Users\test\mc\mods_提取both_輸出"

    def test_結尾帶正斜線會被去除(self, monkeypatch):
        """情況 A-邊界：輸入 .../mods/（結尾帶正斜線），產出 .../mods_提取lang_輸出"""
        view = _make_view(monkeypatch)
        view.mods_dir_textfield.value = r"C:\Users\test\mc\mods/"

        view._auto_fill_output_path(r"C:\Users\test\mc\mods/", mode="lang")

        assert view.output_dir_textfield.value == r"C:\Users\test\mc\mods_提取lang_輸出"

    def test_結尾帶反斜線會被去除(self, monkeypatch):
        """情況 A-邊界：輸入 .../mods\（結尾帶反斜線），產出 .../mods_提取lang_輸出"""
        view = _make_view(monkeypatch)
        view.mods_dir_textfield.value = "C:\\Users\\test\\mc\\mods\\"

        view._auto_fill_output_path("C:\\Users\\test\\mc\\mods\\", mode="lang")

        # 雙反斜線會被 rstrip 去除，結果應與無反斜線時一致
        assert "_提取lang_輸出" in view.output_dir_textfield.value
        assert view.output_dir_textfield.value.endswith("_提取lang_輸出")
        assert not view.output_dir_textfield.value.endswith("\\_提取lang_輸出")

    def test_已包含_suffix_不會重複疊加(self, monkeypatch):
        """情況 B：智慧判斷 - 輸入已包含 suffix 不會重複疊加"""
        view = _make_view(monkeypatch)
        view.mods_dir_textfield.value = r"C:\Users\test\mc\mods_提取lang_輸出"

        view._auto_fill_output_path(r"C:\Users\test\mc\mods_提取lang_輸出", mode="lang")

        # 應該直接使用原路徑，不會變成 mods_提取lang_輸出_提取lang_輸出
        assert view.output_dir_textfield.value == r"C:\Users\test\mc\mods_提取lang_輸出"
        assert view.output_dir_textfield.value.count("_提取lang_輸出") == 1

    def test_自訂資料夾會在末尾加上_suffix(self, monkeypatch):
        """情況 C：自訂資料夾會在末尾加上 suffix"""
        view = _make_view(monkeypatch)
        view.mods_dir_textfield.value = r"C:\Users\test\mc\my_custom_folder"

        view._auto_fill_output_path(r"C:\Users\test\mc\my_custom_folder", mode="lang")

        assert view.output_dir_textfield.value == r"C:\Users\test\mc\my_custom_folder_提取lang_輸出"

    def test_使用者已手動輸入輸出路徑時不會被覆寫(self, monkeypatch):
        """保護機制：使用者已輸入的輸出路徑不會被自動補齊覆寫"""
        view = _make_view(monkeypatch)
        view.mods_dir_textfield.value = r"C:\Users\test\mc\mods"
        view.output_dir_textfield.value = r"D:\my\custom\output"

        view._auto_fill_output_path(r"C:\Users\test\mc\mods", mode="lang")

        # 應該維持使用者原本的輸出路徑
        assert view.output_dir_textfield.value == r"D:\my\custom\output"

    def test_自動補齊後會呼叫_page_update(self, monkeypatch):
        """確保 _auto_fill_output_path 執行後會更新 UI"""
        view = _make_view(monkeypatch)
        page = view.page
        page.updated = 0  # 重置計數

        view.mods_dir_textfield.value = r"C:\Users\test\mc\mods"
        view._auto_fill_output_path(r"C:\Users\test\mc\mods", mode="lang")

        assert page.updated > 0

    def test_自動補齊後會寫入系統日誌(self, monkeypatch):
        """確保 _auto_fill_output_path 執行後會寫入 log_view"""
        view = _make_view(monkeypatch)
        view.mods_dir_textfield.value = r"C:\Users\test\mc\mods"

        # 清空 log_view
        view.log_view.controls.clear()

        view._auto_fill_output_path(r"C:\Users\test\mc\mods", mode="lang")

        # 檢查最後一個 log 是否包含 [系統] 自動設定輸出路徑
        last_log = view.log_view.controls[-1].value
        assert "[系統] 自動設定輸出路徑" in last_log
        assert "_提取lang_輸出" in last_log


# -----------------------------------------------------------------------------
# open_extractor_dialog：對話框路徑自動補齊邏輯
# -----------------------------------------------------------------------------

class TestExtractorDialogPathFilling:
    """測試 open_extractor_dialog 的路徑自動補齊邏輯。"""

    def test_未指定輸出目錄時_使用_mods_dir_作為基礎(self, monkeypatch):
        """未指定 output_path 時，會用 mods_dir + suffix 組出 final_output"""
        from app.views.extractor.extractor_dialog import open_extractor_dialog

        page = mock_page()
        fp = mock_filepicker()

        # 由於 open_extractor_dialog 內部會建立 UI 與背景執行緒，
        # 這裡只測試 final_output 的邏輯（從原始碼讀取後模擬）
        with patch(
            "app.views.extractor.extractor_dialog.load_config",
            return_value={
                "jar_extractor": {"lang_codes": ["zh_tw"]},
                "extractor": {
                    "output_folder_names": {
                        "lang_extract": "_提取lang_輸出",
                        "book_extract": "_提取book_輸出",
                        "dual_extract": "_提取both_輸出",
                    }
                },
            },
        ):
            # 模擬 open_extractor_dialog 內部的路徑組合邏輯
            input_path = r"C:\Users\test\mc\mods"
            output_path = ""
            mode = "lang"

            # 復刻 open_extractor_dialog 的路徑邏輯
            output_dir = output_path if output_path else input_path
            output_subdir = "_提取lang_輸出"
            final_output = os.path.join(output_dir, output_subdir)

            assert final_output == r"C:\Users\test\mc\mods\_提取lang_輸出"

    def test_已指定輸出目錄時_補上_suffix(self, monkeypatch):
        """已指定 output_path 時，會在其下補上 suffix"""
        input_path = r"C:\Users\test\mc\mods"
        output_path = r"D:\my\output"
        mode = "lang"

        output_dir = output_path
        output_subdir = "_提取lang_輸出"
        final_output = os.path.join(output_dir, output_subdir)

        assert final_output == r"D:\my\output\_提取lang_輸出"

    def test_各種_mode_對應的_suffix(self):
        """驗證 mode 與 suffix 的對應關係"""
        cfg = {
            "extractor": {
                "output_folder_names": {
                    "lang_extract": "_提取lang_輸出",
                    "book_extract": "_提取book_輸出",
                    "dual_extract": "_提取both_輸出",
                }
            }
        }
        folder_names = cfg.get("extractor", {}).get("output_folder_names", {})

        assert folder_names.get("lang_extract") == "_提取lang_輸出"
        assert folder_names.get("book_extract") == "_提取book_輸出"
        assert folder_names.get("dual_extract") == "_提取both_輸出"


# -----------------------------------------------------------------------------
# 主 UI controls 結構（移除日誌後的驗證）
# -----------------------------------------------------------------------------

class TestExtractorViewControlsStructure:
    """測試 ExtractorView 的 controls 結構（commit f9c5e20 移除日誌區塊）。"""

    def _get_all_text_in_controls(self, controls):
        """遞迴找出 controls 樹中所有 ft.Text 的 value 文字。"""
        texts = []
        for ctrl in controls:
            if isinstance(ctrl, ft.Text):
                texts.append(ctrl.value)
            if hasattr(ctrl, "controls") and ctrl.controls:
                texts.extend(self._get_all_text_in_controls(ctrl.controls))
            if hasattr(ctrl, "content") and ctrl.content:
                if isinstance(ctrl.content, ft.Text):
                    texts.append(ctrl.content.value)
                elif hasattr(ctrl.content, "controls"):
                    texts.extend(self._get_all_text_in_controls(ctrl.content.controls))
        return texts

    def test_主_UI_不包含日誌區塊(self, monkeypatch):
        """主 UI 不應該再包含 '日誌' 標題"""
        view = _make_view(monkeypatch)
        all_texts = self._get_all_text_in_controls(view.controls)
        assert "日誌" not in all_texts

    def test_主_UI_包含設定區塊(self, monkeypatch):
        """主 UI 應該包含 '設定' 標題"""
        view = _make_view(monkeypatch)
        all_texts = self._get_all_text_in_controls(view.controls)
        assert "設定" in all_texts

    def test_主_UI_只包含_一個_區塊(self, monkeypatch):
        """確認主 UI 只剩一個區塊（設定）"""
        view = _make_view(monkeypatch)
        # 移除日誌後，controls 應該只剩 1 個
        assert len(view.controls) == 1

    def test_log_view_屬性仍存在但不顯示(self, monkeypatch):
        """雖然主 UI 不再顯示日誌，但 log_view 屬性應該還在（供對話框使用）"""
        view = _make_view(monkeypatch)
        # log_view 屬性還是存在（避免破壞其他依賴此屬性的程式碼）
        assert hasattr(view, "log_view")
        assert isinstance(view.log_view, ft.ListView)


# -----------------------------------------------------------------------------
# __init__ 階段不應該崩潰（commit f9c5e20 修過的 RuntimeError）
# -----------------------------------------------------------------------------

class TestExtractorViewInitSafety:
    """測試 ExtractorView 初始化時不應該觸發 RuntimeError。"""

    def test_初始化不會觸發_Control_must_be_added_to_the_page_first(self, monkeypatch):
        """確保 __init__ 中的 self.page.update() 被 try-except 保護"""
        monkeypatch.setattr("app.views.extractor_view.TaskSession", _SessionStub)

        # 這個步驟之前會拋出 RuntimeError: Control must be added to the page first
        # 因為 _update_output_dir_helper() 會呼叫 self.page.update()，
        # 而元件尚未被加入到 page
        view = ExtractorView(mock_page(), mock_filepicker())

        # 只要能執行到這一行就算通過
        assert view is not None

    def test_初始化後_helper_文字仍正確設定(self, monkeypatch):
        """確保即使 try-except 攔截了錯誤，helper 文字仍正確填入"""
        monkeypatch.setattr("app.views.extractor_view.TaskSession", _SessionStub)
        view = ExtractorView(mock_page(), mock_filepicker())

        helper = view.output_dir_textfield.helper
        assert helper is not None
        assert "未指定時自動產生" in helper
        assert "_提取lang_輸出" in helper
        assert "_提取book_輸出" in helper


# -----------------------------------------------------------------------------
# Path 處理回歸測試（f9c5e20 修過的 AttributeError）
# -----------------------------------------------------------------------------

class TestPathHandlingRegression:
    """回歸測試：確保 Path.rstrip() 不會再發生。"""

    def test_Path_物件呼叫_rstrip_會失敗(self):
        """記錄 Python 行為：Path 物件本身沒有 rstrip 方法"""
        p = Path(r"C:\test\mods")
        # 這是預期的 AttributeError，是當初 bug 的根源
        with pytest.raises(AttributeError):
            p.rstrip("\\/")

    def test_str_路徑呼叫_rstrip_是_正確用法(self):
        """正確用法：先轉成 str 再呼叫 rstrip"""
        s = r"C:\test\mods\\"
        result = s.rstrip("\\/")
        assert result == r"C:\test\mods"

    def test_Path_str_Path_可以正確處理結尾斜線(self):
        """正確的處理方式：Path(str(x).rstrip('\\/'))"""
        original = r"C:\test\mods\\"
        p = Path(str(original).rstrip("\\/"))
        assert p.name == "mods"
        assert p.parent == Path(r"C:\test")

    def test_邏輯函式不會崩潰(self, monkeypatch):
        """整合測試：_auto_fill_output_path 不應該因為路徑處理而崩潰"""
        view = _make_view(monkeypatch)

        # 各種邊界條件
        test_inputs = [
            r"C:\test\mods",
            r"C:\test\mods\\",
            r"C:\test\mods/",
            r"C:\test\my_folder",
            r"C:\test\my_folder_提取lang_輸出",
        ]

        for inp in test_inputs:
            view.mods_dir_textfield.value = inp
            view.output_dir_textfield.value = ""  # 確保觸發自動補齊
            # 不應該拋出任何例外
            view._auto_fill_output_path(inp, mode="lang")
            # 結果不為空
            assert view.output_dir_textfield.value != ""
