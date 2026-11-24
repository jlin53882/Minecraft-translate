"""QC 頁的格式一致性檢查：en_us／zh_tw 逐檔比對，列出特殊字元不一致的條目。"""

from __future__ import annotations

import json
from pathlib import Path

from translation_tool.checkers.format_consistency_checker import (
    check_format_consistency_generator,
    find_format_mismatches,
)


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_find_format_mismatches_reports_only_inconsistent_strings():
    en = {
        "gold": "§6Gold %s ingot",
        "ok": "§6Gold %s ingot",
        "named": "Value {count}",
        "plain": "Plain text",
        "no_tw": "Only English",
        "empty_tw": "Hello %s",
        "list": ["a", "b"],
    }
    tw = {
        "gold": "黃金錠",  # 少了 §6 與 %s
        "ok": "§6黃金%s錠",
        "named": "{數量} 個",  # 具名佔位符被改名
        "plain": "純文字",
        "empty_tw": "  ",
        "list": ["甲", "乙"],
        "only_tw": "多出來的",
    }

    found = find_format_mismatches(en, tw)

    assert set(found) == {"gold", "named"}
    assert found["gold"]["en_us"] == "§6Gold %s ingot"
    assert found["gold"]["zh_tw"] == "黃金錠"
    assert any("%s" in issue for issue in found["gold"]["issues"])


def test_generator_writes_report_with_relative_paths_and_summary(tmp_path):
    en_dir, tw_dir, out_dir = tmp_path / "en", tmp_path / "tw", tmp_path / "out"
    _write(en_dir / "assets/foo/lang/en_us.json", {"a": "§6Gold %s", "b": "Fine"})
    _write(tw_dir / "assets/foo/lang/en_us.json", {"a": "黃金", "b": "很好"})
    _write(en_dir / "assets/bar/lang/en_us.json", {"c": "Hello %s"})
    _write(tw_dir / "assets/bar/lang/en_us.json", {"c": "你好 %s"})
    _write(en_dir / "assets/baz/lang/en_us.json", {"d": "No tw file"})  # 沒有對應繁中檔

    updates = list(
        check_format_consistency_generator(str(en_dir), str(tw_dir), str(out_dir))
    )

    assert not any(u.get("error") for u in updates)
    report = json.loads(
        (out_dir / "assets/foo/lang/en_us.json").read_text(encoding="utf-8")
    )
    assert list(report) == ["a"]
    assert not (out_dir / "assets/bar/lang/en_us.json").exists()
    assert not (out_dir / "assets/baz/lang/en_us.json").exists()
    summary = " ".join(u["log"] for u in updates)
    assert "1 個特殊字元不一致" in summary
    assert "沒有對應的繁中檔" in summary


def test_generator_reports_when_no_source_files(tmp_path):
    (tmp_path / "en").mkdir()
    updates = list(
        check_format_consistency_generator(
            str(tmp_path / "en"), str(tmp_path / "tw"), str(tmp_path / "out")
        )
    )
    assert updates[-1]["error"] is True


def test_generator_keeps_going_after_a_broken_file(tmp_path):
    en_dir, tw_dir, out_dir = tmp_path / "en", tmp_path / "tw", tmp_path / "out"
    _write(en_dir / "a.json", {"x": "%s"})
    (tw_dir).mkdir()
    (tw_dir / "a.json").write_text("{壞掉", encoding="utf-8")
    _write(en_dir / "b.json", {"y": "§6Hi"})
    _write(tw_dir / "b.json", {"y": "嗨"})

    updates = list(
        check_format_consistency_generator(str(en_dir), str(tw_dir), str(out_dir))
    )

    assert any(u.get("error") for u in updates)
    assert (out_dir / "b.json").exists()
