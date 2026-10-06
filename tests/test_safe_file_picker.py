"""Web 模式不支援的選擇器不拋例外；輸出資料夾自動建立。"""

import asyncio

import flet as ft
import pytest
from flet.controls.exceptions import FletUnsupportedPlatformException

from app.ui import safe_file_picker as sfp
from app.ui.safe_file_picker import SafeFilePicker, ensure_output_dir


def _raise(*a, **k):
    async def go():
        raise FletUnsupportedPlatformException("web")

    return go()


@pytest.mark.parametrize("method", ["get_directory_path", "pick_files", "save_file"])
def test_unsupported_platform_returns_none_and_shows_hint(monkeypatch, method):
    shown = []
    monkeypatch.setattr(ft.FilePicker, method, _raise)
    monkeypatch.setattr(sfp, "show_snack", lambda page, msg, **k: shown.append(msg))
    monkeypatch.setattr(SafeFilePicker, "page", property(lambda s: "PAGE"))

    result = asyncio.run(getattr(SafeFilePicker(), method)())

    assert result is None
    assert shown == [sfp.WEB_UNSUPPORTED_HINT]


def test_unmounted_picker_still_returns_none(monkeypatch):
    monkeypatch.setattr(ft.FilePicker, "get_directory_path", _raise)

    def boom(*a, **k):
        raise RuntimeError("not mounted")

    monkeypatch.setattr(sfp, "show_snack", boom)
    monkeypatch.setattr(SafeFilePicker, "page", property(lambda s: "PAGE"))

    assert asyncio.run(SafeFilePicker().get_directory_path()) is None


def test_supported_result_passes_through(monkeypatch):
    async def ok(self, *a, **k):
        return "C:/picked"

    monkeypatch.setattr(ft.FilePicker, "get_directory_path", ok)
    assert asyncio.run(SafeFilePicker().get_directory_path()) == "C:/picked"


def test_other_exceptions_are_not_swallowed(monkeypatch):
    async def bad(self, *a, **k):
        raise ValueError("real bug")

    monkeypatch.setattr(ft.FilePicker, "get_directory_path", bad)
    with pytest.raises(ValueError):
        asyncio.run(SafeFilePicker().get_directory_path())


def test_ensure_output_dir_creates_missing(tmp_path):
    target = tmp_path / "new" / "out"
    assert ensure_output_dir(str(target)) is None
    assert target.is_dir()


def test_ensure_output_dir_rejects_empty_and_file(tmp_path):
    assert ensure_output_dir("  ") == "請輸入輸出目錄"
    f = tmp_path / "f.txt"
    f.write_text("x")
    assert "是檔案" in ensure_output_dir(str(f))


def test_ensure_output_dir_reports_uncreatable(tmp_path):
    blocker = tmp_path / "f"
    blocker.write_text("x")
    # 父層是檔案：makedirs 失敗 → 轉成可顯示的錯誤，不拋例外
    assert ensure_output_dir(str(blocker / "sub")) == "輸出目錄無法建立"


def test_app_never_builds_a_raw_file_picker():
    """新增選擇器一律用 SafeFilePicker，Web 模式才不會拋未捕捉的例外。"""
    import ast
    from pathlib import Path

    offenders = []
    app_dir = Path(__file__).resolve().parent.parent / "app"
    files = list(app_dir.rglob("*.py"))
    assert len(files) > 50  # 找不到檔案時不能假通過
    for path in files:
        if path.name == "safe_file_picker.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "FilePicker"
            ):
                offenders.append(f"{path}:{node.lineno}")
    assert not offenders, offenders


def _prepare(tmp_path, output):
    from types import SimpleNamespace

    from app.views.pipeline import pipeline_view as pv

    mods = tmp_path / "mods"
    mods.mkdir()
    shown = []
    view = object.__new__(pv.PipelineView)
    view._page = None
    view.input_path_text = SimpleNamespace(value=str(mods))
    view.output_path_text = SimpleNamespace(value=str(output))
    return view, shown


def test_one_click_creates_missing_output_dir(tmp_path, monkeypatch):
    from app.views.pipeline import pipeline_view as pv

    view, shown = _prepare(tmp_path, tmp_path / "fresh" / "out")
    monkeypatch.setattr(pv, "show_snack", lambda page, msg, **k: shown.append(msg))
    prepared = view._prepare_one_click({"mode": "lang", "lang_codes": ["zh_tw"]})
    assert prepared is not None and shown == []
    assert (tmp_path / "fresh" / "out").is_dir()


def test_one_click_rejects_empty_output(tmp_path, monkeypatch):
    from app.views.pipeline import pipeline_view as pv

    view, shown = _prepare(tmp_path, "")
    monkeypatch.setattr(pv, "show_snack", lambda page, msg, **k: shown.append(msg))
    assert view._prepare_one_click({"mode": "lang", "lang_codes": ["zh_tw"]}) is None
    assert shown == ["❌ 請輸入輸出目錄"]


def test_save_file_on_web_is_unsupported_before_flet_raises_value_error(monkeypatch):
    """Flet Web 的 save_file 沒有 src_bytes 會拋 ValueError（不是 FletUnsupportedPlatformException）。"""
    shown = []
    called = []

    async def would_raise(self, *a, **k):
        called.append(1)
        raise ValueError('"src_bytes" is required when saving a file in web mode')

    monkeypatch.setattr(ft.FilePicker, "save_file", would_raise)
    monkeypatch.setattr(sfp, "show_snack", lambda page, msg, **k: shown.append(msg))
    monkeypatch.setattr(
        SafeFilePicker, "page", property(lambda s: type("P", (), {"web": True})())
    )

    result = asyncio.run(SafeFilePicker().save_file(file_name="x.zip"))

    assert result is None
    assert called == []  # 根本沒進 Flet 的 save_file
    assert shown == [sfp.WEB_UNSUPPORTED_HINT]


def test_save_file_on_desktop_still_surfaces_real_value_errors(monkeypatch):
    async def real_error(self, *a, **k):
        raise ValueError("桌面版真正的錯誤")

    monkeypatch.setattr(ft.FilePicker, "save_file", real_error)
    monkeypatch.setattr(
        SafeFilePicker, "page", property(lambda s: type("P", (), {"web": False})())
    )

    with pytest.raises(ValueError):
        asyncio.run(SafeFilePicker().save_file(file_name="x.zip"))


def test_save_file_on_desktop_passes_through(monkeypatch):
    async def ok(self, *a, **k):
        return "C:/out/x.zip"

    monkeypatch.setattr(ft.FilePicker, "save_file", ok)
    monkeypatch.setattr(
        SafeFilePicker, "page", property(lambda s: type("P", (), {"web": False})())
    )

    assert asyncio.run(SafeFilePicker().save_file(file_name="x.zip")) == "C:/out/x.zip"
