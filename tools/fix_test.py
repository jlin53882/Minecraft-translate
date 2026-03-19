# -*- coding: utf-8 -*-
"""修補 test_all_features.py 的問題（ idempotent 版本）"""
from pathlib import Path

script = Path("tools/test_all_features.py")
content = script.read_text(encoding="utf-8")

# ── 1. 重建頂部的路徑設定（移除舊的，寫入正確版本） ──────────────────
old_header = """# ── 確保翻譯工具可在無 UI 環境匯入 ──────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent  # tools/
SRC_ROOT     = PROJECT_ROOT.parent     # �M�׮ڥؿ�  # tools/
SRC_ROOT     = PROJECT_ROOT.parent     # �M�׮ڥؿ��]translation_tool / app �b�o�^
sys.path.insert(0, str(SRC_ROOT))"""

new_header = """# ── 確保翻譯工具可在無 UI 環境匯入 ──────────────────────────────────────
# PROJECT_ROOT = tools/ 目錄（目前未用到，保留相容）
PROJECT_ROOT = Path(__file__).parent
# SRC_ROOT     = 專案根目錄（translation_tool / app 在這）
SRC_ROOT     = PROJECT_ROOT.parent
sys.path.insert(0, str(SRC_ROOT))"""

if old_header in content:
    content = content.replace(old_header, new_header)
    print("header 修補完成")
else:
    # 嘗試簡單版本
    old2 = 'PROJECT_ROOT = Path(__file__).parent  # tools/\nSRC_ROOT     = PROJECT_ROOT.parent     # �M�׮ڥؿ�  # tools/\nSRC_ROOT     = PROJECT_ROOT.parent     # �M�׮ڥؿ��]translation_tool / app �b�o�^'
    new2 = 'PROJECT_ROOT = Path(__file__).parent\nSRC_ROOT     = PROJECT_ROOT.parent'
    if old2 in content:
        content = content.replace(old2, new2)
        print("header (方法2) 修補完成")
    else:
        print("WARNING: header 找不到精確匹配，檢查中...")

# ── 2. 修補 test_qc 的 mkdir ──────────────────────────────────────────
old_mkdir = """        (en_dir / "test_mod").mkdir()
        (tw_dir / "test_mod").mkdir()"""
new_mkdir = """        mod_en = en_dir / "test_mod" / "lang"
        mod_tw = tw_dir / "test_mod" / "lang"
        mod_en.mkdir(parents=True); mod_tw.mkdir(parents=True)"""
if old_mkdir in content:
    content = content.replace(old_mkdir, new_mkdir)
    print("test_qc mkdir 修補完成")
else:
    print("WARNING: test_qc mkdir 找不到目標")

# ── 3. 修復 PROJECT_ROOT / "replace_rules.json" → SRC_ROOT ────────────
content = content.replace(
    'rules_path = PROJECT_ROOT / "replace_rules.json"',
    'rules_path = SRC_ROOT / "replace_rules.json"'
)
print("replace_rules.json 路徑修補完成")

script.write_text(content, encoding="utf-8")
print("修補完成，現在執行測試...")
