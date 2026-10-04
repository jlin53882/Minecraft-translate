"""tests/test_dead_code_detection.py
用途：靜態分析確認 ftb_translator_export / ftb_translator_template 有實際 caller（非死碼）。
"""
from pathlib import Path


def test_ftb_translator_modules_have_callers():
    """確認 ftb_translator_export 與 ftb_translator_template 有實際被引用（不是死碼）。"""
    repo_root = Path(__file__).parent.parent
    core_dir = repo_root / "translation_tool" / "core"

    # 搜尋翻譯模組：排除自身 + __pycache__
    py_files = [
        f for f in core_dir.glob("**/*.py")
        if "__pycache__" not in str(f)
    ]
    assert len(py_files) > 0

    callers_export = []
    callers_template = []

    for f in py_files:
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if "ftb_translator_export" in content:
            callers_export.append(str(f.relative_to(repo_root)))
        if "ftb_translator_template" in content:
            callers_template.append(str(f.relative_to(repo_root)))

    assert len(callers_export) > 0, (
        "ftb_translator_export 無任何 caller（可能為死碼）\n"
        f"搜尋範圍：{len(py_files)} 個 .py 檔案"
    )
    assert len(callers_template) > 0, (
        "ftb_translator_template 無任何 caller（可能為死碼）\n"
        f"搜尋範圍：{len(py_files)} 個 .py 檔案"
    )
