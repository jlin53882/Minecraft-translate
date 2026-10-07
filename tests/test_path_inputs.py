"""路徑輸入欄位：貼上帶引號的路徑（Windows 檔案總管「複製為路徑」）要自動去掉引號。"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ui import kit
from app.ui.sync_text_field import SyncTextField
from translation_tool.utils.config_manager import resolve_project_path
from translation_tool.utils.path_text import normalize_path_text, strip_path_quotes

ROOT = Path(__file__).resolve().parents[1]
QUOTED = r'"C:\Users\Jlin5\OneDrive\桌面\.minecraft\versions\All the Mods 11\mods"'
CLEAN = r"C:\Users\Jlin5\OneDrive\桌面\.minecraft\versions\All the Mods 11\mods"


def test_path_text_helpers():
    assert normalize_path_text(QUOTED) == CLEAN
    assert normalize_path_text(f"  '{CLEAN}'  ") == CLEAN
    assert normalize_path_text("“a b”") == "a b"
    assert normalize_path_text(None) == "" and normalize_path_text("") == ""
    assert (
        strip_path_quotes(f'"{CLEAN}" ') == CLEAN
    )  # 整段被引號包住：連引號外的空白一併去掉
    assert (
        strip_path_quotes(f"{CLEAN} ") == f"{CLEAN} "
    )  # 沒有引號：不動空白（可能還在打字）


def test_path_field_strips_quotes_on_paste_and_trims_on_blur():
    field = kit.text_field(hint="資料夾", path=True)
    assert field.path_input is True
    field.on_change(SimpleNamespace(control=field, data=QUOTED, name="change"))
    assert field.value == CLEAN  # 貼上的瞬間就去掉引號

    # 輸入中不動空白（路徑中間可能還在打字），離開欄位才整理
    field.on_change(SimpleNamespace(control=field, data=f"  {CLEAN}  ", name="change"))
    assert field.value == f"  {CLEAN}  "
    field.on_blur(SimpleNamespace(control=field, data=None, name="blur"))
    assert field.value == CLEAN


def test_non_path_fields_are_left_alone():
    field = kit.text_field(hint="搜尋")
    assert field.path_input is False
    field.on_change(SimpleNamespace(control=field, data='"quoted"', name="change"))
    assert field.value == '"quoted"'


def test_user_handler_sees_the_cleaned_value():
    seen: list[str] = []
    field = kit.text_field(
        hint="資料夾", path=True, on_change=lambda e: seen.append(e.control.value)
    )
    field.on_change(SimpleNamespace(control=field, data=QUOTED, name="change"))
    assert seen == [CLEAN]


def test_resolve_project_path_accepts_quoted_paths(tmp_path):
    assert resolve_project_path(f'"{tmp_path}"') == tmp_path
    assert resolve_project_path(f"'{tmp_path}'") == tmp_path
    assert resolve_project_path("") == resolve_project_path(".")


_PATH_WORDS = ("資料夾", "目錄", "路徑", "ZIP 檔案", "Mod 來源", "pack.png", "翻譯目標")


def _path_like_fields() -> list[tuple[str, int, str]]:
    """畫面層裡「看起來是路徑」的可編輯輸入欄位；回傳沒有開啟 path 的清單。"""
    missing: list[tuple[str, int, str]] = []
    for path in sorted((ROOT / "app" / "views").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if name not in ("text_field", "SyncTextField"):
                continue
            kwargs = {k.arg: k.value for k in node.keywords if k.arg}
            texts = [
                ast.get_source_segment(source, n) or ""
                for n in [
                    *node.args[:1],
                    kwargs.get("label"),
                    kwargs.get("hint"),
                    kwargs.get("hint_text"),
                ]
                if n is not None
            ]
            if not any(word in text for text in texts for word in _PATH_WORDS):
                continue
            read_only = kwargs.get("read_only")
            if (
                read_only is not None
                and ast.get_source_segment(source, read_only) == "True"
            ):
                continue
            flag = kwargs.get("path") or kwargs.get("path_input")
            if flag is None or ast.get_source_segment(source, flag) != "True":
                missing.append(
                    (
                        str(path.relative_to(ROOT)),
                        node.lineno,
                        texts[0][:30] if texts else "",
                    )
                )
    return missing


def test_every_path_like_input_field_enables_path_cleaning():
    missing = _path_like_fields()
    assert missing == [], (
        "這些路徑輸入欄位沒有開啟 path（貼上帶引號的路徑不會自動去引號）："
        + repr(missing)
    )


@pytest.mark.parametrize(
    "setting",
    ["translation_db.path", "translator.replace_rules_path", "logging.log_dir"],
)
def test_config_path_settings_use_path_cleaning(setting):
    from app.views.config.settings_form import make_control
    from app.views.config.settings_schema import SETTINGS_BY_PATH

    assert make_control(SETTINGS_BY_PATH[setting]).path_input is True


def test_sync_text_field_default_is_not_a_path_field():
    assert SyncTextField(label="x").path_input is False
