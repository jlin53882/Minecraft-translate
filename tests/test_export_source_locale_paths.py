"""匯出的繁中資源包不應出現 en_us／zh_cn 語系路徑。

涵蓋三種實際模組包遇到的格式：
- 帶序號的語系資料夾（Ice and Fire 圖鑑 ``lang/bestiary/en_us_0``、``zh_cn_0``）
- 語系寫在檔名尾端（Tombstone ``text/<名稱>_en_us.txt``）
- GuideME 的 ``_zh_cn`` 支援檔（圖片、結構）只當鏡像來源，不進資源包
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services_impl.pipelines.bundle_service import (
    _carries_source_locale,
    build_bundle_staging,
)
from translation_tool.core.guide_translation import guide_output_rel
from translation_tool.core.lang_merge_content_copy import (
    process_content_or_copy_file_impl,
)
from translation_tool.core.pipeline_content_audit import _pending_guide_rel


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "assets/iceandfire/lang/bestiary/en_us_0/alchemy_0.txt",
            "assets/iceandfire/lang/bestiary/zh_tw_0/alchemy_0.txt",
        ),
        (
            "assets/tombstone/text/coral_chant_1_en_us.txt",
            "assets/tombstone/text/coral_chant_1_zh_tw.txt",
        ),
        (
            "assets/demo/ae2guide/_en_us/chapter/page.md",
            "assets/demo/ae2guide/_zh_tw/chapter/page.md",
        ),
    ],
)
def test_guide_output_replaces_existing_locale_token(
    tmp_path: Path, source: str, expected: str
):
    assert guide_output_rel(Path(source), tmp_path) == Path(expected)


@pytest.mark.parametrize(
    "source",
    [
        "assets/iceandfire/lang/bestiary/en_us_0/alchemy_0.txt",
        "assets/tombstone/text/coral_chant_1_en_us.txt",
    ],
)
def test_pending_guide_keeps_source_locale_token(source: str):
    """原文已帶語系記號時不再補一層 ``_en_us`` 資料夾。"""

    assert _pending_guide_rel(Path(source)) == Path(source)


def _copy_kwargs(tmp_path: Path, writes: dict[str, str]) -> dict:
    reader = MagicMock()
    reader.read_bytes.return_value = "簡體內容".encode()
    return {
        "reader": reader,
        "rules": [],
        "output_dir": str(tmp_path / "out"),
        "other_output_dir": str(tmp_path / "other"),
        "all_files_cache": ["assets/x"],
        "wrapper_prefix": None,
        "load_config_fn": lambda: {
            "lang_merger": {},
            "lm_translator": {"patchouli": {"dir_names": ["patchouli_books"]}},
        },
        "recursive_translate_dict_fn": lambda value, rules: value,
        "get_text_processor_fn": lambda ext: lambda raw, fn, rules, path: raw,
        "write_bytes_atomic_fn": lambda path, data: None,
        "write_text_atomic_fn": lambda path, text: writes.__setitem__(
            Path(path).relative_to(tmp_path).as_posix(), text
        ),
        "quarantine_copy_fn": lambda **kwargs: None,
        "normalize_patchouli_book_root_fn": lambda root: root,
        "patch_localized_content_json_fn": lambda *args, **kwargs: {"success": True},
        "json_module": json,
    }


def test_numbered_zh_cn_folder_is_converted_to_zh_tw(tmp_path: Path):
    writes: dict[str, str] = {}

    result = process_content_or_copy_file_impl(
        input_path="assets/iceandfire/lang/bestiary/zh_cn_0/alchemy_0.txt",
        **_copy_kwargs(tmp_path, writes),
    )

    assert result["success"] is True
    assert list(writes) == ["out/assets/iceandfire/lang/bestiary/zh_tw_0/alchemy_0.txt"]


@pytest.mark.parametrize(
    ("rel", "dropped"),
    [
        ("assets/demo/guides/demo/guide/_zh_cn/images/a.png", True),
        ("assets/iceandfire/lang/bestiary/zh_cn_0/alchemy_0.txt", True),
        ("assets/iceandfire/lang/_zh_tw/bestiary/en_us_0/alchemy_0.txt", True),
        ("assets/tombstone/text/_zh_tw/coral_chant_1_en_us.txt", True),
        ("assets/tombstone/text/coral_chant_1_zh_tw.txt", False),
        ("assets/demo/guides/demo/guide/_zh_tw/images/a.png", False),
        ("assets/demo/lang/zh_tw.json", False),
        ("assets/demo/textures/gui/en_us_button.png", False),
    ],
)
def test_carries_source_locale(rel: str, dropped: bool):
    assert _carries_source_locale(str(Path(rel))) is dropped


def test_zh_cn_guide_support_is_mirror_source_but_not_bundled(tmp_path: Path):
    source = tmp_path / "merge"
    guide = source / "assets/demo/guides/demo/guide"
    (guide / "_zh_tw").mkdir(parents=True)
    (guide / "_zh_tw/index.md").write_text("# 指南\n\n![](images/a.png)\n", "utf-8")
    (guide / "_zh_cn/images").mkdir(parents=True)
    (guide / "_zh_cn/images/a.png").write_bytes(b"zh_cn image")
    staging = tmp_path / "staging"

    stats = build_bundle_staging([str(source)], str(staging))

    staged = staging / "assets/demo/guides/demo/guide"
    assert (staged / "_zh_tw/images/a.png").read_bytes() == b"zh_cn image"
    assert not (staged / "_zh_cn").exists()
    manifest = json.loads(Path(stats["manifest_path"]).read_text(encoding="utf-8"))
    dropped = [
        record
        for record in manifest["records"]
        if record["output"].endswith("_zh_cn/images/a.png")
    ]
    assert [(r["status"], r["reason"]) for r in dropped] == [
        ("excluded", "source_locale_not_bundled")
    ]
