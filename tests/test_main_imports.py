import ast
from pathlib import Path


def test_main_does_not_import_disabled_views():
    """main.py 不應 import 被停用的頁面。

    依規則：查詢/品管/輸出打包/Icon 校對 會重寫或刪除，
    相關程式先不設計、不誤動，所以 main.py 也先不要 import。

    用 AST 解析可避免被註解內容干擾。
    """

    main_path = Path(__file__).resolve().parents[1] / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"))

    forbidden_modules = {
        "app.views.lookup_view",
        "app.views.qc_view",
        "app.views.bundler_view",
        "app.views.icon_preview_view",
    }

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert forbidden_modules.isdisjoint(imported)
