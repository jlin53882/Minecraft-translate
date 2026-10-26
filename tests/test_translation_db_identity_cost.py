"""項目身分（classify_file）在翻譯時每個項目都會算一次：不能每次深拷貝設定或解析實體路徑。"""

from __future__ import annotations

import os
from pathlib import Path

from translation_tool.translation_db import identity
from translation_tool.translation_db.schema import KIND_LANG
from translation_tool.utils import config_manager


def test_classify_file_does_not_resolve_paths_for_files_under_root(
    tmp_path, monkeypatch
):
    target = tmp_path / "assets" / "foo" / "lang" / "en_us.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")

    def fail_resolve(self, *args, **kwargs):
        raise AssertionError("不應為了取得相對路徑而解析實體路徑")

    monkeypatch.setattr(Path, "resolve", fail_resolve)

    found = identity.classify_file(target, tmp_path)

    assert found is not None
    assert (found.kind, found.mod_id, found.lang) == (KIND_LANG, "foo", "en_us")


def test_classify_file_still_resolves_symlinked_or_foreign_paths(tmp_path):
    real_root = tmp_path / "real"
    target = real_root / "assets" / "foo" / "lang" / "en_us.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")
    link_root = tmp_path / "link"
    try:
        os.symlink(real_root, link_root, target_is_directory=True)
    except (OSError, NotImplementedError):
        return  # 這個環境不能建立 symlink

    found = identity.classify_file(
        link_root / "assets" / "foo" / "lang" / "en_us.json", real_root
    )

    assert found is not None and found.mod_id == "foo"


def test_classify_file_with_parent_references_is_normalised(tmp_path):
    target = tmp_path / "assets" / "foo" / "lang" / "en_us.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")

    found = identity.classify_file(
        tmp_path / "x" / ".." / "assets" / "foo" / "lang" / "en_us.json", tmp_path
    )

    assert found is not None and found.mod_id == "foo"


def test_patchouli_dir_names_reads_the_shared_config_without_copying(monkeypatch):
    def fail_copy(*args, **kwargs):
        raise AssertionError("身分判斷不應深拷貝整份設定")

    monkeypatch.setattr(config_manager, "load_config", fail_copy)
    monkeypatch.setattr(
        config_manager,
        "load_config_shared",
        lambda *a, **k: {"lm_translator": {"patchouli": {"dir_names": ["my_books"]}}},
    )

    assert identity.patchouli_dir_names() == ("my_books",)


def test_patchouli_dir_names_falls_back_to_defaults_when_config_unavailable(
    monkeypatch,
):
    def broken(*args, **kwargs):
        raise OSError("config unreadable")

    monkeypatch.setattr(config_manager, "load_config_shared", broken)

    assert identity.patchouli_dir_names() == identity.DEFAULT_PATCHOULI_DIRS
