"""View／UI／外殼直接 import 引擎核心（translation_tool.core）的棘輪（#136）。

View 經由 ``app/services_impl`` 取用引擎（金鑰健康度：``key_health_service``；圖示：``icon_service``；
提取／打包：``pipelines/extract_service``、``bundle_service``）。白名單目前為空：**不得新增**直接 import；
確有充分理由時才加入白名單並寫明原因。

``translation_tool.utils.*``（log_unit、config_manager…）是純工具，不在此限制內。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("app/views", "app/ui", "app/shell")

# (View 檔案, 引擎模組)：已知的直接依賴與處理方向
ALLOWED: dict[tuple[str, str], str] = {}  # #136：全部已改走 app/services_impl


def _engine_imports() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for scan in SCAN_DIRS:
        for path in sorted((ROOT / scan).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    modules = [node.module]
                elif isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                else:
                    continue
                for module in modules:
                    if module.startswith("translation_tool.") and not module.startswith(
                        "translation_tool.utils"
                    ):
                        found.add((path.relative_to(ROOT).as_posix(), module))
    return found


def test_views_do_not_gain_new_direct_engine_imports():
    new = sorted(_engine_imports() - set(ALLOWED))
    assert new == [], (
        "View／UI／外殼新增了對 translation_tool.core 的直接 import；"
        "請改由 app/services_impl 提供（或有充分理由時加入白名單並說明）：\n"
        + "\n".join(f"{f} → {m}" for f, m in new)
    )


def test_allowlist_has_no_stale_entries():
    stale = sorted(set(ALLOWED) - _engine_imports())
    assert stale == [], "白名單有已經不存在的項目，請移除：\n" + "\n".join(
        f"{f} → {m}" for f, m in stale
    )
