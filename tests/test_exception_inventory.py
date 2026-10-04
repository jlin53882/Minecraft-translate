"""#135：例外與 print 的盤點由 ruff 強制——沒有說明的寬鬆例外、無聲吞掉的例外、非工具程式的 print 都不允許。

規則（``ruff --select BLE001,S110,S112,T201``）：
- 寬鬆的 ``except Exception``（BLE001）、``try/except/pass``（S110）、``try/except/continue``（S112）
  必須有行內 ``# noqa: <規則> - <原因>``，或已經改成有紀錄／回報的處理；
- 程式碼內的 ``print``（T201）改用 logging。

唯一的例外是兩個「命令列 QA 工具」：它們的 stdout 就是產品介面，且刻意以 print 回報略過的檔案。
完整的盤點與分類見 docs/SECURITY_ERROR_HANDLING_AUDIT.md。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# 命令列 QA 工具：stdout 即介面（刻意保留 print 與以 print 回報的寬鬆例外）
CLI_TOOL_ALLOWLIST = {
    "translation_tool/plugins/md/md_extract_qa.py",
    "translation_tool/plugins/md/md_inject_qa.py",
}


def _run_ruff():
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "ruff",
                "check",
                "app",
                "translation_tool",
                "main.py",
                "--select",
                "BLE001,S110,S112,T201",
                "--output-format",
                "json",
                "--no-cache",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:  # pragma: no cover
        pytest.skip("ruff 不可用")
    if proc.returncode not in (0, 1):
        pytest.skip(f"ruff 無法執行：{proc.stderr[:200]}")
    return json.loads(proc.stdout or "[]")


def test_no_unexplained_broad_except_silent_except_or_print():
    findings = _run_ruff()
    offenders = []
    for item in findings:
        path = Path(item["filename"]).resolve().relative_to(REPO_ROOT).as_posix()
        if path in CLI_TOOL_ALLOWLIST:
            continue
        offenders.append(f"{path}:{item['location']['row']} {item['code']}")
    assert not offenders, (
        "以下位置有未說明的寬鬆例外／無聲例外／print；請補紀錄，或加上 "
        "`# noqa: <規則> - <原因>`：\n" + "\n".join(offenders)
    )


def test_allowlist_entries_exist():
    for rel in CLI_TOOL_ALLOWLIST:
        assert (REPO_ROOT / rel).is_file(), rel


# 還沒有寫原因的寬鬆例外豁免註解（BLE001／S110／S112 的 noqa）：每個檔案目前的數量（棘輪）。
# 數量只能減少：新增的寬鬆例外必須在 noqa 後面寫原因（" - 原因"）；補上原因後請把這裡的數字調降。
# 這 N 個位置的處理方式已分類：121 處的例外處理本身已有紀錄／回報／重新丟出（只缺文字說明），
# 其餘為 UI 保護（畫面更新、進度回報）——都不是無聲吞掉資料相關錯誤的路徑；
# 資料相關的無聲路徑已補上紀錄（見 docs/SECURITY_ERROR_HANDLING_AUDIT.md）。
UNEXPLAINED_NOQA_BASELINE = {
    "app/icon_index.py": 5,
    "app/icon_reader.py": 3,
    "app/services_impl/cache/cache_services.py": 2,
    "app/services_impl/pipelines/_task_runner.py": 1,
    "app/services_impl/pipelines/bundle_service.py": 1,
    "app/services_impl/pipelines/extract_service.py": 3,
    "app/services_impl/pipelines/lm_service.py": 1,
    "app/services_impl/pipelines/merge_service.py": 5,
    "app/shell/app_shell.py": 1,
    "app/ui/snack.py": 4,
    "app/views/bundler_view.py": 1,
    "app/views/cache_manager/cache_history_store.py": 4,
    "app/views/cache_manager/cache_view_history.py": 2,
    "app/views/cache_manager/cache_view_overview.py": 2,
    "app/views/cache_manager/cache_view_query.py": 3,
    "app/views/cache_manager/cache_view_shard.py": 9,
    "app/views/cache_manager/cache_view_shard_detail.py": 3,
    "app/views/cache_query_panel.py": 3,
    "app/views/cache_shard_panel.py": 3,
    "app/views/cache_view.py": 6,
    "app/views/extractor/extractor_dialog.py": 1,
    "app/views/icon_preview/detail_mixin.py": 2,
    "app/views/icon_preview/icon_cache.py": 5,
    "app/views/icon_preview_row.py": 1,
    "app/views/merge_view.py": 4,
    "app/views/qc_view.py": 1,
    "app/views/rules/rules_actions.py": 2,
    "app/views/translation/translation_actions.py": 11,
    "app/views/translation_view.py": 4,
    "translation_tool/core/ftb_translator.py": 3,
    "translation_tool/core/jar_processor_extract.py": 3,
    "translation_tool/core/lang_merge_content_copy.py": 9,
    "translation_tool/core/lang_merge_extracted_assets.py": 1,
    "translation_tool/core/lang_merge_pending.py": 1,
    "translation_tool/core/lang_merge_pipeline.py": 3,
    "translation_tool/core/lang_merge_zip_io.py": 1,
    "translation_tool/core/lang_merger.py": 6,
    "translation_tool/core/lm_api_client.py": 1,
    "translation_tool/core/lm_translator.py": 1,
    "translation_tool/core/lm_translator_main.py": 3,
    "translation_tool/core/lm_translator_shared_loop.py": 7,
    "translation_tool/core/md_translation_steps.py": 1,
    "translation_tool/plugins/kubejs/kubejs_tooltip_extract.py": 1,
    "translation_tool/utils/cache_search.py": 1,
    "translation_tool/utils/cache_shards.py": 2,
    "translation_tool/utils/config_manager.py": 1,
    "translation_tool/utils/jar_browser.py": 1,
    "translation_tool/utils/text_processor.py": 4,
}


def _load_generator():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "gen_exception_inventory", REPO_ROOT / "tools" / "gen_exception_inventory.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _count_unexplained_noqa() -> dict[str, int]:
    rows = _load_generator().collect()
    counts: dict[str, int] = {}
    for row in rows:
        if row["reason"].startswith("（未寫原因"):
            counts[row["file"]] = counts.get(row["file"], 0) + 1
    return counts


def test_noqa_without_reason_never_increases():
    current = _count_unexplained_noqa()
    worse = {
        path: (n, UNEXPLAINED_NOQA_BASELINE.get(path, 0))
        for path, n in current.items()
        if n > UNEXPLAINED_NOQA_BASELINE.get(path, 0)
    }
    assert not worse, (
        "新增了沒有原因的寬鬆例外 noqa（請寫成 `# noqa: BLE001 - 原因`）："
        + ", ".join(f"{p}: {n} > {b}" for p, (n, b) in worse.items())
    )


def test_baseline_has_no_stale_entries():
    """原因補上之後請調降基準：基準比實際多（或檔案已清完）時提醒更新。"""
    current = _count_unexplained_noqa()
    stale = {
        p: (b, current.get(p, 0))
        for p, b in UNEXPLAINED_NOQA_BASELINE.items()
        if current.get(p, 0) < b
    }
    assert not stale, "請調降 UNEXPLAINED_NOQA_BASELINE：" + ", ".join(
        f"{p}: 基準 {b} → 實際 {n}" for p, (b, n) in stale.items()
    )


def test_inventory_document_matches_the_code():
    """docs/EXCEPTION_INVENTORY.md 必須與 render(collect()) 逐字一致。

    每一列的檔案、函式、規則、分類、原因都會比對（文件不含行號，所以一般的程式碼移動不會讓它失敗）。
    """
    gen = _load_generator()
    expected = gen.render(gen.collect())
    actual = (REPO_ROOT / "docs" / "EXCEPTION_INVENTORY.md").read_text(encoding="utf-8")
    assert actual == expected, (
        "docs/EXCEPTION_INVENTORY.md 已過期，請執行 `uv run python tools/gen_exception_inventory.py` 重新產生"
    )
