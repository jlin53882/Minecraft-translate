"""PR-B 的 UI／engine import boundary 契約。"""

import ast
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_translation_tool_does_not_import_flet_or_app():
    """整個引擎套件（core／utils／checkers／…）不得反向持有 Flet 或 app UI 相依（#136）。

    函式內的延遲 import 也會被抓到（掃描整棵 AST）。
    """
    engine_root = _repo_root() / "translation_tool"
    violations: list[str] = []
    for path in sorted(engine_root.rglob("*.py")):
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
