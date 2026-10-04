"""PR-B 的 UI／engine import boundary 契約。"""

import ast
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_translation_tool_core_does_not_import_flet_or_app():
    """核心模組不得反向持有 Flet 或 app UI 相依。"""
    core_root = _repo_root() / "translation_tool" / "core"
    violations: list[str] = []
    for path in sorted(core_root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if (
                    name == "flet"
                    or name.startswith(("flet.", "app."))
                    or name == "app"
                ):
                    violations.append(
                        f"{path.relative_to(_repo_root())}:{node.lineno}: {name}"
                    )

    assert violations == [], "engine → UI import boundary violated:\n" + "\n".join(
        violations
    )


def test_icon_preview_row_is_the_ui_owner_for_lang_item_row():
    from app.views.icon_preview_row import LangItemRow

    assert LangItemRow.__module__ == "app.views.icon_preview_row"
