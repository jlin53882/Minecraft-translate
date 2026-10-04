"""#135：FTB 翻譯 callback 內原本無聲的失敗，現在必須留下紀錄且不中斷翻譯。"""

from __future__ import annotations

from types import SimpleNamespace

from translation_tool.plugins.ftbquests import ftbquests_lmtranslator as ftb


class _Rec:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    def record(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("recorder down")


def _item():
    return {
        "path": "quest.title",
        "text": "已翻譯",
        "source_text": "Quest",
        "_shielded": SimpleNamespace(shields=["<tag>"]),
    }


def test_unshield_failure_is_logged_and_text_is_still_stored(monkeypatch, tmp_path):
    warnings: list[str] = []
    monkeypatch.setattr(ftb, "log_warning", warnings.append)

    def boom(text, shields):
        raise ValueError("bad shield")

    monkeypatch.setattr(ftb, "unshield_text", boom)
    out_map: dict = {}
    cb = ftb._make_on_translated_item(
        "a.json", tmp_path / "out" / "a.json", out_map, _Rec(), tmp_path / "out"
    )

    cb(_item())

    assert out_map == {"quest.title": "已翻譯"}  # 翻譯結果不因還原失敗而遺失
    assert any("還原保護標記失敗" in w and "quest.title" in w for w in warnings)


def test_recorder_failure_is_logged_and_does_not_break_translation(
    monkeypatch, tmp_path
):
    warnings: list[str] = []
    monkeypatch.setattr(ftb, "log_warning", warnings.append)
    monkeypatch.setattr(ftb, "unshield_text", lambda t, s: t)
    out_map: dict = {}
    rec = _Rec(fail=True)
    cb = ftb._make_on_translated_item(
        "a.json", tmp_path / "out" / "a.json", out_map, rec, tmp_path / "out"
    )

    cb(_item())

    assert rec.calls == 1
    assert out_map == {"quest.title": "已翻譯"}
    assert any("記錄翻譯結果失敗" in w for w in warnings)


def test_batch_flush_failure_falls_back_to_direct_write_and_logs(monkeypatch, tmp_path):
    warnings: list[str] = []
    monkeypatch.setattr(ftb, "log_warning", warnings.append)
    written: list[tuple] = []
    monkeypatch.setattr(
        ftb, "write_json_dict", lambda dst, data: written.append((dst, data))
    )

    class BrokenTouch:
        def touch(self, file_id):
            raise OSError("disk full")

        def flush(self, writer):
            raise AssertionError("不應走到")

    dst = tmp_path / "a.json"
    cb = ftb._make_on_batch_flushed(
        "fid", BrokenTouch(), lambda *a: None, dst, {"k": "v"}
    )
    cb()

    assert written == [(dst, {"k": "v"})]
    assert any("批次刷新失敗" in w for w in warnings)
