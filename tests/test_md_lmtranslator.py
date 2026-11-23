"""translation_tool/plugins/md/md_lmtranslator.py 模組測試。

用途：測試 md_lmtranslator 模組的功能。
"""

from __future__ import annotations

import json
from pathlib import Path

# 測試模組
from translation_tool.plugins.md import md_lmtranslator


def test_read_json(tmp_path: Path) -> None:
    """測試 read_json 讀取 JSON。"""
    json_file = tmp_path / "test.json"
    json_file.write_text(json.dumps({"key": "value"}))

    result = md_lmtranslator.read_json(json_file)

    assert result == {"key": "value"}


def test_write_json(tmp_path: Path) -> None:
    """測試 write_json 寫入 JSON。"""
    json_file = tmp_path / "output.json"

    md_lmtranslator.write_json(json_file, {"key": "value"})

    assert json_file.exists()
    assert json.loads(json_file.read_text()) == {"key": "value"}


def test_write_json_creates_parent(tmp_path: Path) -> None:
    """測試 write_json 建立父目錄。"""
    json_file = tmp_path / "subdir" / "output.json"

    md_lmtranslator.write_json(json_file, {"data": 123})

    assert json_file.exists()
    assert json_file.parent.exists()


def test_collect_pending_json_files(tmp_path: Path) -> None:
    """測試 collect_pending_json_files 收集 JSON 檔案。"""
    # 建立測試結構
    pending_root = tmp_path / "pending"
    pending_root.mkdir()

    # 一般 JSON 檔案
    (pending_root / "file1.json").write_text("{}")
    (pending_root / "file2.json").write_text("{}")

    # Manifest 檔案（應該被跳過）
    (pending_root / "_manifest.json").write_text("{}")

    # 子目錄中的 JSON
    subdir = pending_root / "subdir"
    subdir.mkdir()
    (subdir / "file3.json").write_text("{}")

    files = md_lmtranslator.collect_pending_json_files(pending_root)

    # 應該有 3 個檔案（排除 manifest）
    assert len(files) == 3
    assert all("_manifest" not in str(f) for f in files)


def test_load_pending_doc(tmp_path: Path) -> None:
    """測試 load_pending_doc 載入待翻譯文件。"""
    # 建立測試 JSON
    json_data = {
        "schema": "md_pending_blocks_v1",
        "source_md": "test.md",
        "items": [
            {
                "id": "test.md:1-2",
                "text": "Hello",
                "content_hash": "abc123",
                "start_line": 1,
                "end_line": 2,
            }
        ],
    }

    json_file = tmp_path / "test.json"
    json_file.write_text(json.dumps(json_data))

    data, items = md_lmtranslator.load_pending_doc(json_file)

    assert data["schema"] == "md_pending_blocks_v1"
    assert len(items) == 1
    assert items[0].text == "Hello"


def test_load_pending_doc_invalid_schema(tmp_path: Path) -> None:
    """測試 load_pending_doc 無效 schema。"""
    json_file = tmp_path / "test.json"
    json_file.write_text(json.dumps({"schema": "unknown", "items": []}))

    try:
        md_lmtranslator.load_pending_doc(json_file)
        assert False, "應該拋出 ValueError"
    except ValueError:
        pass


def test_compute_out_json_path(tmp_path: Path) -> None:
    """測試 compute_out_json_path 計算輸出路徑。"""
    src_json = tmp_path / "pending" / "test.json"
    src_json.parent.mkdir(parents=True)
    src_json.write_text("{}")

    in_pending_root = tmp_path / "pending"
    out_root = tmp_path / "output"

    result = md_lmtranslator.compute_out_json_path(src_json, in_pending_root, out_root)

    # out_root 就是譯後資料夾本身：直接放在底下，不會再多一層
    assert result == out_root / "test.json"


def test_pending_item_dataclass(tmp_path: Path) -> None:
    """測試 PendingItem 資料類別。"""
    item = md_lmtranslator.PendingItem(
        id="test:1-2",
        text="Hello World",
        content_hash="abc123",
        start_line=1,
        end_line=2,
    )

    assert item.id == "test:1-2"
    assert item.text == "Hello World"
    assert item.content_hash == "abc123"
    assert item.start_line == 1
    assert item.end_line == 2


def test_md_skip_reason_item_stays_original_in_dry_run_inputs() -> None:
    """skip_reason 項目在 MD 流程中應保留原文。"""
    shielded = md_lmtranslator.shield_text("https://example.com")
    assert shielded.skip_reason == "url"
    assert shielded.clean == "https://example.com"


def _write_pending(root: Path, text: str) -> Path:
    pending = root / "待翻譯"
    pending.mkdir()
    (pending / "a.md.json").write_text(
        json.dumps(
            {
                "schema": "md_pending_blocks_v1",
                "source_md": "a.md",
                "items": [
                    {
                        "id": "a.md:1-1",
                        "text": text,
                        "content_hash": "h1",
                        "start_line": 1,
                        "end_line": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return pending


def _no_valid_key(monkeypatch) -> None:
    def _raise() -> None:
        raise ValueError("沒有有效的 API 金鑰")

    monkeypatch.setattr(md_lmtranslator, "validate_api_keys", _raise)


def test_zero_block_pending_does_not_need_api_key(tmp_path: Path, monkeypatch) -> None:
    """沒有區塊要翻（例如 zh_cn 底稿殘留區塊為零）時不檢查金鑰，仍寫出 DONE 譯後檔。"""
    _no_valid_key(monkeypatch)
    pending = _write_pending(tmp_path, "   ")
    out = tmp_path / "LM翻譯後"

    result = md_lmtranslator.translate_md_pending(pending_dir=pending, out_dir=out)

    assert result["status"] == "DONE"
    assert result["written_files"] == 1
    done = json.loads((out / "a.md.json").read_text(encoding="utf-8"))
    assert done["step2_status"] == "DONE"


def test_pending_with_blocks_still_requires_api_key(
    tmp_path: Path, monkeypatch
) -> None:
    """真的有區塊要送 API 時，沒有有效金鑰仍要在呼叫前就失敗。"""
    import pytest

    _no_valid_key(monkeypatch)
    pending = _write_pending(tmp_path, "Right click the controller.")

    with pytest.raises(ValueError, match="API"):
        md_lmtranslator.translate_md_pending(
            pending_dir=pending, out_dir=tmp_path / "out"
        )


def test_dry_run_does_not_need_api_key(tmp_path: Path, monkeypatch) -> None:
    """dry-run 不呼叫 API，沒有金鑰也能預覽。"""
    _no_valid_key(monkeypatch)
    pending = _write_pending(tmp_path, "Right click the controller.")

    result = md_lmtranslator.translate_md_pending(
        pending_dir=pending, out_dir=tmp_path / "out", dry_run=True
    )

    assert result["status"] == "DRY_RUN"
