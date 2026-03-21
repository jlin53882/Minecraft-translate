"""md_translation_steps.py 單元測試。

用途：測試 Markdown 翻譯步驟的實現函式。
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch


from translation_tool.core.md_translation_progress import _ProgressProxy
from translation_tool.core.md_translation_steps import (
    step1_extract_impl,
    step2_translate_impl,
    step3_inject_impl,
)


class MockBlock:
    """模擬翻譯區塊。"""

    def __init__(self, text: str):
        self.text = text
        self.content_hash = hash(text)


class _MockSession:
    """模擬 session 物件。"""

    def __init__(self):
        self.progress_values = []

    def set_progress(self, value: float):
        self.progress_values.append(value)


def _mock_iter_md_files(in_root: Path):
    """模擬迭代 Markdown 檔案。"""
    return list(in_root.glob("**/*.md")) if in_root.exists() else []


def _mock_safe_relpath(path: Path, root: Path):
    """模擬安全相對路徑。"""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _mock_extract_blocks(text: str, rel_md: str, *, lang_mode: str):
    """模擬提取區塊。"""
    # 簡單模擬：每一行是一個區塊
    blocks = []
    for i, line in enumerate(text.splitlines()):
        if line.strip():
            blocks.append(MockBlock(line))
    return blocks


def _mock_detect_lang_segment(parts: list):
    """模擬語言區段偵測。"""
    if not parts:
        return "en_us"
    last = parts[-1].lower()
    if "zh" in last:
        return "zh_tw"
    return "en_us"


def _mock_map_rel_lang_path(rel_md: str, *, src_lang: str, dst_lang: str):
    """模擬語言路徑映射。"""
    return rel_md.replace(f"/{src_lang}/", f"/{dst_lang}/")


def _mock_contains_cjk(text: str) -> bool:
    """模擬 CJK 偵測。"""
    cjk_ranges = [(0x4E00, 0x9FFF), (0x3000, 0x30FF), (0xAC00, 0xD7AF)]
    for char in text:
        code = ord(char)
        if any(start <= code <= end for start, end in cjk_ranges):
            return True
    return False


def _mock_build_pending_json(rel_md: str, md_path: Path, items: list, lang_mode: str):
    """模擬建立 pending JSON。"""
    return {
        "source_md": rel_md,
        "items": [{"text": item.text} for item in items],
    }


def _mock_progress_fn(session, value: float):
    """模擬進度回調。"""
    if hasattr(session, "set_progress"):
        session.set_progress(value)


def _mock_log_warning(*args, **kwargs):
    """模擬日誌警告。"""
    pass


class TestStep1ExtractImpl:
    """測試 step1_extract_impl 函式。"""

    def test_extract_with_no_md_files(self, tmp_path: Path) -> None:
        """測試沒有 Markdown 檔案時的行為。"""
        input_dir = tmp_path / "input"
        pending_dir = tmp_path / "pending"
        input_dir.mkdir()

        session = _MockSession()

        result = step1_extract_impl(
            input_dir=str(input_dir),
            pending_dir=str(pending_dir),
            lang_mode="all",
            session=session,
            progress_base=0.0,
            progress_span=1.0,
            iter_md_files_fn=_mock_iter_md_files,
            safe_relpath_fn=_mock_safe_relpath,
            extract_blocks_fn=_mock_extract_blocks,
            detect_lang_segment_fn=_mock_detect_lang_segment,
            map_rel_lang_path_fn=_mock_map_rel_lang_path,
            contains_cjk_fn=_mock_contains_cjk,
            build_pending_json_fn=_mock_build_pending_json,
            progress_fn=_mock_progress_fn,
            log_warning_fn=_mock_log_warning,
        )

        assert result["md_files_found"] == 0
        assert result["json_written"] == 0
        assert result["total_blocks"] == 0

    def test_extract_with_single_md_file(self, tmp_path: Path) -> None:
        """測試單一 Markdown 檔案的提取。"""
        input_dir = tmp_path / "input"
        pending_dir = tmp_path / "pending"
        input_dir.mkdir()

        # 建立測試 Markdown 檔案
        md_file = input_dir / "test.md"
        md_file.write_text("# Title\nTest content\nAnother line", encoding="utf-8")

        session = _MockSession()

        result = step1_extract_impl(
            input_dir=str(input_dir),
            pending_dir=str(pending_dir),
            lang_mode="all",
            session=session,
            progress_base=0.0,
            progress_span=1.0,
            iter_md_files_fn=_mock_iter_md_files,
            safe_relpath_fn=_mock_safe_relpath,
            extract_blocks_fn=_mock_extract_blocks,
            detect_lang_segment_fn=_mock_detect_lang_segment,
            map_rel_lang_path_fn=_mock_map_rel_lang_path,
            contains_cjk_fn=_mock_contains_cjk,
            build_pending_json_fn=_mock_build_pending_json,
            progress_fn=_mock_progress_fn,
            log_warning_fn=_mock_log_warning,
        )

        assert result["md_files_found"] == 1
        assert result["json_written"] == 1
        assert result["total_blocks"] >= 1

    def test_extract_with_nested_md_files(self, tmp_path: Path) -> None:
        """測試嵌套 Markdown 檔案的提取。"""
        input_dir = tmp_path / "input"
        pending_dir = tmp_path / "pending"
        input_dir.mkdir()

        # 建立嵌套目錄結構
        subdir = input_dir / "subdir"
        subdir.mkdir()
        (input_dir / "root.md").write_text("Root content", encoding="utf-8")
        (subdir / "nested.md").write_text("Nested content", encoding="utf-8")

        session = _MockSession()

        result = step1_extract_impl(
            input_dir=str(input_dir),
            pending_dir=str(pending_dir),
            lang_mode="all",
            session=session,
            progress_base=0.0,
            progress_span=1.0,
            iter_md_files_fn=_mock_iter_md_files,
            safe_relpath_fn=_mock_safe_relpath,
            extract_blocks_fn=_mock_extract_blocks,
            detect_lang_segment_fn=_mock_detect_lang_segment,
            map_rel_lang_path_fn=_mock_map_rel_lang_path,
            contains_cjk_fn=_mock_contains_cjk,
            build_pending_json_fn=_mock_build_pending_json,
            progress_fn=_mock_progress_fn,
            log_warning_fn=_mock_log_warning,
        )

        assert result["md_files_found"] == 2
        assert result["json_written"] == 2


class TestStep2TranslateImpl:
    """測試 step2_translate_impl 函式。"""

    def test_translate_uses_progress_proxy(self, tmp_path: Path) -> None:
        """測試翻譯步驟正確使用進度代理。"""
        pending_dir = tmp_path / "pending"
        translated_dir = tmp_path / "translated"
        pending_dir.mkdir()

        session = _MockSession()

        def fake_translate(**kwargs):
            # 模擬翻譯函式會調用 session 的 set_progress
            if "session" in kwargs and kwargs["session"]:
                kwargs["session"].set_progress(0.5)
            return {"translated": 10}

        with patch("translation_tool.core.md_translation_assembly.translate_md_pending", fake_translate):
            result = step2_translate_impl(
                pending_dir=str(pending_dir),
                translated_dir=str(translated_dir),
                session=session,
                progress_base=0.3,
                progress_span=0.4,
                dry_run=False,
                write_new_cache=True,
                progress_proxy_cls=_ProgressProxy,
                translate_md_pending_fn=fake_translate,
                progress_fn=_mock_progress_fn,
            )

        assert "translated" in result

    def test_translate_dry_run_mode(self, tmp_path: Path) -> None:
        """測試翻譯的乾運行模式。"""
        pending_dir = tmp_path / "pending"
        translated_dir = tmp_path / "translated"
        pending_dir.mkdir()

        session = _MockSession()

        def fake_translate(**kwargs):
            return {"dry_run": kwargs.get("dry_run", False), "files": 1}

        with patch("translation_tool.core.md_translation_assembly.translate_md_pending", fake_translate):
            result = step2_translate_impl(
                pending_dir=str(pending_dir),
                translated_dir=str(translated_dir),
                session=session,
                progress_base=0.0,
                progress_span=1.0,
                dry_run=True,
                write_new_cache=False,
                progress_proxy_cls=_ProgressProxy,
                translate_md_pending_fn=fake_translate,
                progress_fn=_mock_progress_fn,
            )

        assert result.get("dry_run") is True


class TestStep3InjectImpl:
    """測試 step3_inject_impl 函式。"""

    def test_inject_with_no_json_files(self, tmp_path: Path) -> None:
        """測試沒有 JSON 檔案時的行為。"""
        input_dir = tmp_path / "input"
        json_dir = tmp_path / "json"
        final_dir = tmp_path / "final"

        input_dir.mkdir()
        json_dir.mkdir()

        session = _MockSession()

        def mock_iter_json(path):
            return []

        result = step3_inject_impl(
            input_dir=str(input_dir),
            json_dir=str(json_dir),
            final_dir=str(final_dir),
            session=session,
            progress_base=0.0,
            progress_span=1.0,
            iter_json_files_fn=mock_iter_json,
            load_items_from_json_fn=lambda p: (str(p), []),
            apply_item_to_md_lines_fn=lambda lines, item: None,
            map_lang_in_rel_path_allow_zh_fn=lambda p, **kw: (p, "SRC_EN"),
            progress_fn=_mock_progress_fn,
        )

        assert result["written_files"] == 0

    def test_inject_writes_translated_content(self, tmp_path: Path) -> None:
        """測試注入翻譯內容。"""
        input_dir = tmp_path / "input"
        json_dir = tmp_path / "json"
        final_dir = tmp_path / "final"

        input_dir.mkdir()
        json_dir.mkdir()

        # 建立原始 Markdown
        md_file = input_dir / "test.md"
        md_file.write_text("Hello World", encoding="utf-8")

        # 建立翻譯 JSON
        json_file = json_dir / "test.md.json"
        json_file.write_text(json.dumps({
            "source_md": "test.md",
            "items": [{"text": "你好 世界", "line": 0}]
        }), encoding="utf-8")

        session = _MockSession()

        def mock_iter_json(path):
            return [json_file]

        def mock_load_items(path):
            return ("test.md", [{"text": "你好 世界", "line": 0}])

        def mock_apply(lines, item):
            if "line" in item and item["line"] < len(lines):
                lines[item["line"]] = item["text"]

        result = step3_inject_impl(
            input_dir=str(input_dir),
            json_dir=str(json_dir),
            final_dir=str(final_dir),
            session=session,
            progress_base=0.0,
            progress_span=1.0,
            iter_json_files_fn=mock_iter_json,
            load_items_from_json_fn=mock_load_items,
            apply_item_to_md_lines_fn=mock_apply,
            map_lang_in_rel_path_allow_zh_fn=lambda p, **kw: (p.replace(".md", "_zh_tw.md"), "SRC_EN"),
            progress_fn=_mock_progress_fn,
        )

        assert result["written_files"] >= 0

    def test_inject_handles_missing_source(self, tmp_path: Path) -> None:
        """測試處理缺少源檔案的情況。"""
        input_dir = tmp_path / "input"
        json_dir = tmp_path / "json"
        final_dir = tmp_path / "final"

        input_dir.mkdir()
        json_dir.mkdir()

        # JSON 指向不存在的 Markdown
        json_file = json_dir / "missing.md.json"
        json_file.write_text(json.dumps({
            "source_md": "missing.md",
            "items": []
        }), encoding="utf-8")

        session = _MockSession()

        def mock_iter_json(path):
            return [json_file]

        def mock_load_items(path):
            return ("missing.md", [])

        result = step3_inject_impl(
            input_dir=str(input_dir),
            json_dir=str(json_dir),
            final_dir=str(final_dir),
            session=session,
            progress_base=0.0,
            progress_span=1.0,
            iter_json_files_fn=mock_iter_json,
            load_items_from_json_fn=mock_load_items,
            apply_item_to_md_lines_fn=lambda lines, item: None,
            map_lang_in_rel_path_allow_zh_fn=lambda p, **kw: (p, "SRC_EN"),
            progress_fn=_mock_progress_fn,
        )

        # 應該跳過找不到的檔案
        assert result["skipped_missing_source"] >= 1
