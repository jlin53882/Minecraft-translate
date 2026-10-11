"""文件本地連結檢查：避免分類遷移後連結或檢查器本身 false green。"""

from __future__ import annotations

from pathlib import Path

from tools.check_doc_links import find_broken_links


def test_repository_doc_links_are_valid():
    assert find_broken_links() == []


def test_checker_handles_encoded_paths_anchors_and_fenced_examples(tmp_path: Path):
    docs = tmp_path / "docs"
    target = docs / "資料" / "頁面 一.md"
    image = docs / "圖片" / "封面.png"
    target.parent.mkdir(parents=True)
    image.parent.mkdir(parents=True)
    target.write_text(
        '# 頁面\n\n<a id="custom-anchor"></a>\n## 詳細資料\n',
        encoding="utf-8",
    )
    image.write_bytes(b"png")
    (tmp_path / "README.md").write_text(
        """# 測試

[文件](docs/%E8%B3%87%E6%96%99/%E9%A0%81%E9%9D%A2%20%E4%B8%80.md#%E8%A9%B3%E7%B4%B0%E8%B3%87%E6%96%99)
[自訂錨點](docs/%E8%B3%87%E6%96%99/%E9%A0%81%E9%9D%A2%20%E4%B8%80.md#custom-anchor)
![圖片](docs/%E5%9C%96%E7%89%87/%E5%B0%81%E9%9D%A2.png)
[外部](https://example.invalid/missing.md#no-check)

```markdown
[範例](docs/not-real.md)
```
""",
        encoding="utf-8",
    )

    assert find_broken_links(tmp_path) == []


def test_checker_reports_missing_path_and_anchor(tmp_path: Path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "target.md").write_text("# 只有這個標題\n", encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "[不存在](docs/missing.md)\n[錯誤錨點](docs/target.md#missing)\n",
        encoding="utf-8",
    )

    problems = find_broken_links(tmp_path)
    assert any("missing target" in item for item in problems)
    assert any("missing anchor" in item for item in problems)
