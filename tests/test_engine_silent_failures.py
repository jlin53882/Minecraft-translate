"""#135：引擎端原本無聲的失敗（checkpoint、分片序號、隔離檔寫入、強制輪替）現在都會留下紀錄。"""

from __future__ import annotations

import logging

from translation_tool.core import lang_merge_io, lm_translator
from translation_tool.utils import cache_shards


def test_corrupt_checkpoint_is_logged_and_treated_as_missing(tmp_path, monkeypatch):
    path = tmp_path / "checkpoint.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(lm_translator, "CHECKPOINT_FILE", str(path))
    warnings: list[str] = []
    monkeypatch.setattr(lm_translator, "log_warning", warnings.append)

    assert lm_translator.load_checkpoint() is None
    assert any("checkpoint" in w for w in warnings)


def test_missing_checkpoint_is_silent(tmp_path, monkeypatch):
    monkeypatch.setattr(lm_translator, "CHECKPOINT_FILE", str(tmp_path / "none.json"))
    warnings: list[str] = []
    monkeypatch.setattr(lm_translator, "log_warning", warnings.append)
    assert lm_translator.load_checkpoint() is None
    assert warnings == []  # 沒有 checkpoint 是正常情況，不該警告


def test_corrupt_shard_order_file_is_logged_but_missing_one_is_not(tmp_path, caplog):
    assert cache_shards._read_shard_order(tmp_path) == {}  # 檔案不存在：正常
    (tmp_path / cache_shards.SHARD_ORDER_FILE).write_text("{bad", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger=cache_shards.__name__):
        assert cache_shards._read_shard_order(tmp_path) == {}
    assert any("分片序號" in r.getMessage() for r in caplog.records)


def test_quarantine_write_failure_is_logged(tmp_path, monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(lang_merge_io, "log_warning", warnings.append)

    class Reader:
        def read_bytes(self, rel):
            raise OSError("zip gone")

    lang_merge_io.quarantine_copy(
        Reader(),
        "a/b.json",
        str(tmp_path / "out"),
        "reason",
        "detail",
        errordata_dir=str(tmp_path / "quarantine"),
    )  # 不得丟例外

    assert any("隔離檔案寫入失敗" in w for w in warnings)


def test_quarantine_success_writes_copy_reason_and_detail(tmp_path):
    class Reader:
        def read_bytes(self, rel):
            return b"{}"

    target_dir = tmp_path / "quarantine"
    lang_merge_io.quarantine_copy(
        Reader(),
        "a/b.json",
        str(tmp_path / "out"),
        "bad json",
        "detail text",
        errordata_dir=str(target_dir),
    )
    target = target_dir / "a" / "b.json"
    assert target.read_bytes() == b"{}"
    assert (target_dir / "a" / "b.json.reason.txt").read_text(
        encoding="utf-8"
    ) == "bad json"
    assert (target_dir / "a" / "b.json.detail.txt").read_text(
        encoding="utf-8"
    ) == "detail text"
