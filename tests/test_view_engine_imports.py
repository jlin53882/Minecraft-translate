"""View／UI／外殼直接 import 引擎核心（translation_tool.core）的棘輪（#136）。

目標方向是 View 經由 ``app/services_impl`` 呼叫引擎；一次改完風險太高，所以先把「目前的直接依賴」
列成白名單並鎖住：**不得新增**，只能隨各 View 的拆分逐步移除（移除時請同步刪掉白名單的項目）。

``translation_tool.utils.*``（log_unit、config_manager…）是純工具，不在此限制內。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("app/views", "app/ui", "app/shell")

# (View 檔案, 引擎模組)：已知的直接依賴與處理方向
ALLOWED: dict[tuple[str, str], str] = {
    # 金鑰健康度：顯示用的資料類別／常數；快照函式應由 service 提供
    ("app/shell/app_shell.py", "translation_tool.core.lm_config_rules"): "改走 service",
    ("app/shell/topbar.py", "translation_tool.core.lm_key_health"): "顯示用資料類別",
    ("app/views/dashboard/dashboard_data.py", "translation_tool.core.lm_key_health"): "顯示用資料類別",
    ("app/views/dashboard_view.py", "translation_tool.core.lm_config_rules"): "改走 service",
    ("app/views/dashboard_view.py", "translation_tool.core.lm_key_health"): "顯示用資料類別",
    ("app/views/lm_view.py", "translation_tool.core.lm_config_rules"): "改走 service",
    ("app/views/config_view.py", "translation_tool.core.lm_config_rules"): "改走 service",
    # 打包／提取：對話框與頁面直接呼叫引擎
    ("app/views/bundler_view.py", "translation_tool.core.output_bundler"): "改走 service",
    ("app/views/extractor/extractor_dialog.py", "translation_tool.core.jar_processor"): "改走 service",
    ("app/views/extractor/extractor_preview_dialog.py", "translation_tool.core.jar_processor"): "改走 service",
    ("app/views/pipeline/pipeline_extract_dialog.py", "translation_tool.core.jar_processor"): "改走 service",
    # 圖示預覽列：圖示解析與預覽快取
    ("app/views/icon_preview_row.py", "translation_tool.core.icon_preview_cache"): "改走 service",
    ("app/views/icon_preview_row.py", "translation_tool.core.icon_reason"): "顯示用資料類別",
    ("app/views/icon_preview_row.py", "translation_tool.core.icon_resolver"): "改走 service",
}  # fmt: skip


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
