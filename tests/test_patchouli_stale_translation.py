"""Patchouli 手冊：譯文與 en_us 格式不同時不採用譯文，改列待翻譯。"""

from __future__ import annotations

from pathlib import Path

import orjson
import pytest

from translation_tool.core import lang_merge_content, lang_merge_pipeline, lang_merger
from translation_tool.core.patchouli_compat import (
    count_old_nbt_items,
    find_patchouli_marker_issue,
    find_patchouli_mismatch,
)

NEW_ITEM = "laserio:card_item[laserio:card_transfer_mode=0b]"
OLD_ITEM = "laserio:card_item{channel:0b,mode:0b,sneaky:-1b}"


def _entry(*pages: dict) -> dict:
    return {"name": "Modes", "category": "x:y", "pages": list(pages)}


def _spotlight(item: str, text: str) -> dict:
    return {"type": "patchouli:spotlight", "item": item, "text": text}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (OLD_ITEM, 1),
        ("minecraft:stone", 0),
        (NEW_ITEM, 0),
        ("modes{not an item}", 0),
        ("see laserio:card_item{x}", 0),
        (["a:b{c}", {"item": "d:e{f}"}], 2),
        (3, 0),
    ],
)
def test_count_old_nbt_items(text, expected):
    assert count_old_nbt_items(text) == expected


def test_same_shape_is_accepted():
    en = _entry(_spotlight(NEW_ITEM, "Insert"))
    zh = _entry(_spotlight(NEW_ITEM, "輸入"))
    assert find_patchouli_mismatch(en, zh) is None


def test_old_nbt_item_is_rejected_even_if_shape_matches():
    en = _entry(_spotlight(NEW_ITEM, "Insert"))
    zh = _entry(_spotlight(OLD_ITEM, "輸入"))
    assert "NBT" in find_patchouli_mismatch(en, zh)


def test_old_nbt_is_fine_when_the_source_uses_it_too():
    en = _entry(_spotlight(OLD_ITEM, "Insert"))
    zh = _entry(_spotlight(OLD_ITEM, "輸入"))
    assert find_patchouli_mismatch(en, zh) is None


def test_page_count_difference_is_rejected():
    en = _entry(_spotlight(NEW_ITEM, "a"), _spotlight(NEW_ITEM, "b"))
    zh = _entry(_spotlight(NEW_ITEM, "甲"))
    assert "項目數不同" in find_patchouli_mismatch(en, zh)


def test_field_difference_is_rejected():
    en = _entry({"type": "patchouli:text", "text": "a"})
    zh = _entry({"type": "patchouli:text", "text": "甲", "title": "新欄位"})
    assert "欄位不同" in find_patchouli_mismatch(en, zh)


def test_type_difference_is_rejected():
    assert find_patchouli_mismatch({"pages": ["a"]}, {"pages": [{"text": "甲"}]})


def _config() -> dict:
    return {
        "replace_rules_path": "replace_rules.json",
        "translator": {"parallel_execution_workers": 1},
        "lang_merger": {
            "pending_folder_name": "待翻譯",
            "pending_organized_folder_name": "待翻譯整理需翻譯",
            "filtered_pending_min_count": 1,
            "quarantine_folder_name": "skipped_json",
        },
        "lm_translator": {"patchouli": {"dir_names": ["patchouli_books"]}},
    }


def _tag(value, _rules):
    if isinstance(value, dict):
        return {k: _tag(v, _rules) for k, v in value.items()}
    return f"TW:{value}" if isinstance(value, str) else value


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(orjson.dumps(data))


def _merge(monkeypatch, tmp_path: Path, files: dict[str, dict]) -> Path:
    src = tmp_path / "in"
    out = tmp_path / "out"
    out.mkdir()
    for rel, data in files.items():
        _write(src / rel, data)
    monkeypatch.setattr(lang_merger, "load_config", _config)
    monkeypatch.setattr(lang_merger, "load_replace_rules", lambda _p: [])
    monkeypatch.setattr(lang_merge_content, "load_config", _config)
    for module in (lang_merge_content, lang_merge_pipeline):
        monkeypatch.setattr(module, "recursive_translate_dict", _tag)
        monkeypatch.setattr(module, "apply_replace_rules", _tag)
    updates = list(
        lang_merger.merge_zhcn_to_zhtw_from_folder(
            str(src), str(out), only_process_lang=False
        )
    )
    assert all(not u.get("error") for u in updates)
    return out


BOOK = "assets/demo/patchouli_books/guide"


def test_stale_zh_cn_entry_goes_to_pending_and_good_entry_is_converted(
    tmp_path, monkeypatch
):
    out = _merge(
        monkeypatch,
        tmp_path,
        {
            f"{BOOK}/en_us/entries/modes.json": _entry(
                _spotlight(NEW_ITEM, "Insert"), _spotlight(NEW_ITEM, "Extract")
            ),
            f"{BOOK}/zh_cn/entries/modes.json": _entry(_spotlight(OLD_ITEM, "输入")),
            f"{BOOK}/en_us/entries/good.json": _entry(_spotlight(NEW_ITEM, "Hello")),
            f"{BOOK}/zh_cn/entries/good.json": _entry(_spotlight(NEW_ITEM, "你好")),
        },
    )
    patchouli_out = out / "patchouli_output"
    tw = patchouli_out / BOOK / "zh_tw" / "entries"
    assert not (tw / "modes.json").exists()
    assert (tw / "good.json").is_file()
    pending = patchouli_out / "待翻譯" / BOOK / "en_us" / "entries"
    assert (pending / "modes.json").is_file()


def test_stale_zh_tw_does_not_let_the_book_skip_its_english_original(
    tmp_path, monkeypatch
):
    zh_pages = [_spotlight(NEW_ITEM, "好" * 200)] * 3
    files = {
        f"{BOOK}/en_us/entries/stale.json": _entry(*[_spotlight(NEW_ITEM, "a")] * 4),
        f"{BOOK}/zh_tw/entries/stale.json": _entry(*zh_pages),
    }
    for name in ("a", "b", "c"):
        files[f"{BOOK}/en_us/entries/{name}.json"] = _entry(_spotlight(NEW_ITEM, "x"))
        files[f"{BOOK}/zh_tw/entries/{name}.json"] = _entry(
            _spotlight(NEW_ITEM, "好" * 200)
        )
    out = _merge(monkeypatch, tmp_path, files)
    patchouli_out = out / "patchouli_output"
    pending = patchouli_out / "待翻譯" / BOOK / "en_us" / "entries"
    assert (pending / "stale.json").is_file()  # 書本整體翻譯比例夠高，仍要重翻這一個
    assert not (pending / "a.json").exists()  # 格式一致的條目照舊跳過英文原件
    assert not (patchouli_out / BOOK / "zh_tw" / "entries" / "stale.json").exists()


def test_find_old_nbt_item_files_lists_only_patchouli_books(tmp_path):
    bad = tmp_path / "assets/m/patchouli_books/b/zh_tw/entries/x.json"
    good = tmp_path / "assets/m/patchouli_books/b/zh_tw/entries/y.json"
    other = tmp_path / "assets/m/lang/zh_tw.json"
    _write(bad, _entry(_spotlight(OLD_ITEM, "舊")))
    _write(good, _entry(_spotlight(NEW_ITEM, "新")))
    _write(other, {"k": OLD_ITEM})
    from translation_tool.core.patchouli_compat import find_old_nbt_item_files

    assert find_old_nbt_item_files(str(tmp_path)) == [
        "assets/m/patchouli_books/b/zh_tw/entries/x.json"
    ]


def test_bundle_step_warns_about_old_nbt_items(tmp_path):
    from app.tasks.task_session import TaskSession
    from app.views.pipeline.pipeline_actions import PipelineActions, PipelineServices
    from app.views.pipeline.pipeline_config import PipelineConfig

    (tmp_path / "mods").mkdir()
    (tmp_path / "out").mkdir()
    cfg = PipelineConfig(str(tmp_path / "mods"), str(tmp_path / "out"))
    actions = PipelineActions(
        PipelineServices(
            build_staging=lambda *a, **k: {"copied": 1, "merged": 0},
            scan_old_nbt=lambda staging, sources: [
                "assets/m/patchouli_books/b/zh_tw/x.json"
            ],
            bundle=lambda **k: iter([{"progress": 1.0}]),
        )
    )
    step = actions.one_click_steps({}, cfg, "lang", ["en_us"], {})[3][2]
    session = TaskSession(name="一鍵", view_key="pipeline")
    list(step(session))
    texts = [e.text for e in session.snapshot()["logs"]]
    assert any("舊版 NBT" in t and "x.json" in t for t in texts)


@pytest.mark.parametrize(
    ("en_text", "zh_text", "expected"),
    [
        ("Hi$(br)there", "嗨$(br)你好", None),
        ("Hi$(br)there", "嗨你好", "$(br)"),
        ("$(item)Gear$()", "$(item)齒輪", "$()"),
        ("§aGreen", "綠色", "§a"),
        ("Use %s", "使用 %s", None),
        ("Line one\nline two", "第一行第二行", None),
        ("Same text", "Same text", None),
    ],
)
def test_marker_count_difference_is_reported_but_not_a_mismatch(
    en_text, zh_text, expected
):
    en = _entry(_spotlight(NEW_ITEM, en_text))
    zh = _entry(_spotlight(NEW_ITEM, zh_text))
    assert find_patchouli_mismatch(en, zh) is None  # 只影響排版，不丟棄譯文
    reason = find_patchouli_marker_issue(en, zh)
    if expected is None:
        assert reason is None
    else:
        assert "格式標記" in reason and expected in reason
        assert "pages[0].text" in reason


def test_marker_issue_is_skipped_when_the_shape_differs():
    en = _entry(_spotlight(NEW_ITEM, "a$(br)b"), _spotlight(NEW_ITEM, "c"))
    zh = _entry(_spotlight(NEW_ITEM, "ab"))
    assert find_patchouli_marker_issue(en, zh) is None
    assert "項目數" in find_patchouli_mismatch(en, zh)


def test_marker_mismatch_in_lm_output_is_warned_not_dropped(tmp_path, monkeypatch):
    from translation_tool.core import lm_translator

    src_dir = tmp_path / "assets" / "m" / "patchouli_books" / "b" / "en_us" / "entries"
    src_dir.mkdir(parents=True)
    src = src_dir / "e.json"
    en = _entry(_spotlight(NEW_ITEM, "Hi$(br)there"))
    src.write_bytes(orjson.dumps(en))
    warnings: list[str] = []
    monkeypatch.setattr(lm_translator, "log_warning", warnings.append)

    broken = _entry(_spotlight(NEW_ITEM, "嗨你好"))
    lm_translator._write_directory_outputs(
        {str(src): broken}, {str(src)}, tmp_path, tmp_path / "out", False
    )

    assert any("格式標記" in w and "e.json" in w for w in warnings)
    written = tmp_path / "out" / "assets" / "m" / "patchouli_books" / "b" / "zh_tw"
    assert (written / "entries" / "e.json").exists()


def test_old_nbt_files_skip_those_whose_en_us_source_also_uses_it(tmp_path):
    from translation_tool.core.patchouli_compat import find_old_nbt_item_files

    staging, source = tmp_path / "staging", tmp_path / "source"
    book = "assets/m/patchouli_books/b"
    # x：原文就是舊寫法（mod 自己的問題）；y：原文是新寫法、譯文落後；z：找不到原文
    _write(staging / book / "zh_tw/entries/x.json", _entry(_spotlight(OLD_ITEM, "舊")))
    _write(staging / book / "zh_tw/entries/y.json", _entry(_spotlight(OLD_ITEM, "舊")))
    _write(staging / book / "zh_tw/entries/z.json", _entry(_spotlight(OLD_ITEM, "舊")))
    _write(source / book / "en_us/entries/x.json", _entry(_spotlight(OLD_ITEM, "old")))
    _write(source / book / "en_us/entries/y.json", _entry(_spotlight(NEW_ITEM, "new")))

    assert find_old_nbt_item_files(str(staging), [str(source)]) == [
        f"{book}/zh_tw/entries/y.json",
        f"{book}/zh_tw/entries/z.json",
    ]
    # 沒提供原文資料夾時，含舊寫法的檔案全部列入
    assert len(find_old_nbt_item_files(str(staging))) == 3
