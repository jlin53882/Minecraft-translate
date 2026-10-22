"""tests/test_bom_prevention.py
用途：防止 UI 模組檔案帶有 UTF-8 BOM，確保 BOM 被徹底移除且未來不會復發。
"""
from pathlib import Path


def test_ui_python_files_have_no_bom():
    """UI 模組不應以 UTF-8 BOM 開頭。
    BOM (\xef\xbb\xbf) 會干擾模組解析，必須確保所有 app/ui/*.py 都已移除。
    """
    repo_root = Path(__file__).parent.parent
    ui_files = list((repo_root / "app" / "ui").glob("**/*.py"))
    assert len(ui_files) > 0, "找不到任何 app/ui/*.py 檔案"

    bom_files = []
    for f in ui_files:
        with open(f, "rb") as fp:
            first3 = fp.read(3)
        if first3 == b"\xef\xbb\xbf":
            bom_files.append(str(f))

    assert len(bom_files) == 0, (
        f"發現 {len(bom_files)} 個檔案含 BOM：\n" +
        "\n".join(f"  - {f}" for f in bom_files)
    )
