"""合併期間的來源標記（provenance）寫入：整批合併只寫出一次標記檔。

每個語言檔都讀出、重寫並 fsync 整份標記檔會讓合併時間隨模組數平方成長（589 個模組約 55 秒），
所以合併期間改為累積在記憶體、離開時寫出一次。這裡檢查的是寫檔次數與內容，不量牆上時間。
"""

from __future__ import annotations

import json

import pytest

from translation_tool.core import lang_merge_provenance as prov

ENTRY = {
    "label": "資料庫",
    "source": "builtin:x",
    "database_version": "1.20.1",
    "cross_version": False,
    "value": "譯文",
}


@pytest.fixture
def write_count(monkeypatch):
    calls: list[str] = []
    real = prov._write_json_atomic

    def counting(path, payload):
        calls.append(path.name)
        return real(path, payload)

    monkeypatch.setattr(prov, "_write_json_atomic", counting)
    return calls


def _manifest(root):
    return json.loads((root / prov._MANIFEST_NAME).read_text("utf-8"))


def test_without_batch_every_call_is_persisted_immediately(tmp_path, write_count):
    prov.write_translation_provenance(
        tmp_path, "assets/a/lang/zh_tw.json", {"k": ENTRY}
    )
    assert (tmp_path / prov._MANIFEST_NAME).exists()
    prov.write_translation_provenance(
        tmp_path, "assets/b/lang/zh_tw.json", {"k": ENTRY}
    )

    assert len(write_count) == 2


def test_batch_writes_manifest_once_with_the_same_content(tmp_path, write_count):
    immediate = tmp_path / "immediate"
    batched = tmp_path / "batched"
    for root in (immediate, batched):
        root.mkdir()
    names = [f"assets/m{i}/lang/zh_tw.json" for i in range(30)]
    for name in names:
        prov.write_translation_provenance(immediate, name, {"k": ENTRY})
    write_count.clear()

    with prov.provenance_batch(batched):
        for name in names:
            prov.write_translation_provenance(batched, name, {"k": ENTRY})
        assert not (batched / prov._MANIFEST_NAME).exists()

    assert write_count == [prov._MANIFEST_NAME]
    assert _manifest(batched) == _manifest(immediate)


def test_batch_applies_to_nested_output_roots_only(tmp_path, write_count):
    outside = tmp_path / "other"
    outside.mkdir()
    lang_output = tmp_path / "out" / "lang_output"
    lang_output.mkdir(parents=True)

    with prov.provenance_batch(tmp_path / "out"):
        prov.write_translation_provenance(
            lang_output, "assets/a/lang/zh_tw.json", {"k": ENTRY}
        )
        prov.write_translation_provenance(
            outside, "assets/a/lang/zh_tw.json", {"k": ENTRY}
        )
        assert (outside / prov._MANIFEST_NAME).exists()  # 批次外的根目錄照舊立刻寫
        assert not (lang_output / prov._MANIFEST_NAME).exists()

    assert (lang_output / prov._MANIFEST_NAME).exists()


def test_batch_flushes_when_the_merge_is_interrupted(tmp_path):
    with pytest.raises(RuntimeError), prov.provenance_batch(tmp_path):
        prov.write_translation_provenance(
            tmp_path, "assets/a/lang/zh_tw.json", {"k": ENTRY}
        )
        raise RuntimeError("取消")

    assert "assets/a/lang/zh_tw.json" in _manifest(tmp_path)["outputs"]


def test_batch_empty_entries_clear_stale_tags_and_remove_empty_manifest(tmp_path):
    prov.write_translation_provenance(
        tmp_path, "assets/a/lang/zh_tw.json", {"k": ENTRY}
    )

    with prov.provenance_batch(tmp_path):
        prov.write_translation_provenance(tmp_path, "assets/a/lang/zh_tw.json", {})

    assert not (tmp_path / prov._MANIFEST_NAME).exists()


def test_batch_flush_failure_is_logged_not_raised(tmp_path, monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(prov, "log_warning", warnings.append)

    def broken(path, payload):
        raise OSError("disk full")

    monkeypatch.setattr(prov, "_write_json_atomic", broken)

    with prov.provenance_batch(tmp_path):
        prov.write_translation_provenance(
            tmp_path, "assets/a/lang/zh_tw.json", {"k": ENTRY}
        )

    assert warnings and "disk full" in warnings[0]


def test_unsafe_relative_path_is_still_rejected_inside_a_batch(tmp_path):
    with prov.provenance_batch(tmp_path), pytest.raises(ValueError):
        prov.write_translation_provenance(tmp_path, "../escape.json", {"k": ENTRY})
