"""#135：高風險路徑上原本無聲吞掉的失敗，現在必須留下紀錄。"""

from __future__ import annotations

import logging

import orjson

from translation_tool.core import lang_merge_extracted_assets as ext
from translation_tool.core import lang_merge_pending
from translation_tool.utils import cache_overview


def test_unreadable_pending_file_is_logged_and_skipped(tmp_path, monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(lang_merge_pending, "log_warning", warnings.append)
    pending = tmp_path / "pending"
    pending.mkdir()
    (pending / "bad.json").write_bytes(b"{not json")
    (pending / "good.json").write_bytes(orjson.dumps({"a": "1", "b": "2"}))
    out = tmp_path / "out"

    lang_merge_pending.export_filtered_pending_impl(
        str(pending), str(out), 1, json_module=orjson
    )

    assert any("bad.json" in w for w in warnings)
    assert (out / "good.json").exists()
    assert not (out / "bad.json").exists()


def test_active_shard_read_failure_is_logged(tmp_path, caplog):
    class BoomPath:
        @property
        def parent(self):
            raise OSError("disk gone")

    with caplog.at_level(logging.WARNING, logger=cache_overview.log.name):
        result = cache_overview.get_active_shard_id(
            {"lang": BoomPath()}, "lang", ".active"
        )

    assert result == ""
    assert any("作用中分片" in r.getMessage() for r in caplog.records)


def test_session_log_failure_does_not_break_merge(monkeypatch):
    debug: list[str] = []
    monkeypatch.setattr(ext, "log_debug", lambda msg, *a, **k: debug.append(msg))

    class BadSession:
        def add_log(self, message):
            raise RuntimeError("ui gone")

    ext._safe_session_log(BadSession(), "hello")  # 不得丟出例外
    assert debug and "session.add_log" in debug[0]


def test_session_log_success_passes_message():
    got: list[str] = []

    class Session:
        def add_log(self, message):
            got.append(message)

    ext._safe_session_log(Session(), "hello")
    assert got == ["hello"]
