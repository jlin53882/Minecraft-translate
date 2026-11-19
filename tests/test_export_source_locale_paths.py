"""匯出的繁中資源包不應出現 en_us／zh_cn 語系路徑。

涵蓋三種實際模組包遇到的格式：
- 帶序號的語系資料夾（Ice and Fire 圖鑑 ``lang/bestiary/en_us_0``、``zh_cn_0``）
- 語系寫在檔名尾端（Tombstone ``text/<名稱>_en_us.txt``）
- GuideME 的 ``_zh_cn`` 支援檔（圖片、結構）只當鏡像來源，不進資源包
- 放在 Patchouli 名單資料夾內的 GuideME 指南（Powah ``guides/powah/book``）
- 模組自帶繁中時，英文原件不再送 LM 覆蓋官方譯文
"""

from __future__ import annotations

import json
import zipfile
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
from translation_tool.core.locale_path import (
    find_locale_marker,
    has_locale_dir,
    replace_locale,
)
from translation_tool.core.output_bundler import bundle_outputs_generator
from translation_tool.core.pipeline_content_audit import (
    _pending_guide_rel,
    classify_other_output_assets,
)


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


# ---------------------------------------------------------------------------
# 共用語系路徑規則
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rel", "code", "expected"),
    [
        (
            "assets/m/patchouli_books/b/en_us/e.json",
            "zh_tw",
            "assets/m/patchouli_books/b/zh_tw/e.json",
        ),
        ("assets/m/ae2guide/_en_us/p.md", "zh_tw", "assets/m/ae2guide/_zh_tw/p.md"),
        (
            "assets/m/lang/bestiary/en_us_0/a.txt",
            "zh_tw",
            "assets/m/lang/bestiary/zh_tw_0/a.txt",
        ),
        ("assets/m/text/chant_1_en_us.txt", "zh_tw", "assets/m/text/chant_1_zh_tw.txt"),
        ("assets/m/lang/en_us.json", "zh_tw", "assets/m/lang/zh_tw.json"),
    ],
)
def test_replace_locale_keeps_original_style(rel: str, code: str, expected: str):
    parts = Path(rel).parts
    marker = find_locale_marker(parts, "en_us")
    assert marker is not None
    assert Path(*replace_locale(parts, marker, code)) == Path(expected)


def test_locale_markers_ignore_ordinary_names():
    parts = Path("assets/m/textures/gui/en_us_button.png").parts
    assert find_locale_marker(parts, "en_us") is None
    assert has_locale_dir(Path("assets/m/guide/_zh_cn/a.png").parts, "zh_cn")
    assert not has_locale_dir(Path("assets/m/lang/zh_cn.json").parts, "zh_cn")


# ---------------------------------------------------------------------------
# GuideME 指南不走 Patchouli 分支（Powah 把指南放在 guides/powah/book/）
# ---------------------------------------------------------------------------


def _powah_kwargs(tmp_path: Path, writes: dict[str, str]) -> dict:
    kwargs = _copy_kwargs(tmp_path, writes)
    kwargs["load_config_fn"] = lambda: {
        "lang_merger": {},
        "lm_translator": {"patchouli": {"dir_names": ["patchouli_books", "book"]}},
    }
    kwargs["patchouli_output_dir"] = str(tmp_path / "patchouli")
    return kwargs


def test_guideme_zh_cn_under_book_folder_becomes_zh_tw(tmp_path: Path):
    writes: dict[str, str] = {}

    process_content_or_copy_file_impl(
        input_path="assets/powah/guides/powah/book/_zh_cn/index.md",
        **_powah_kwargs(tmp_path, writes),
    )

    assert list(writes) == ["out/assets/powah/guides/powah/book/_zh_tw/index.md"]


def test_guideme_english_under_book_folder_goes_to_other_output(tmp_path: Path):
    """英文原件交給內容分類（送翻譯或因已有官方譯文而略過），不直接打包。"""

    writes: dict[str, str] = {}

    process_content_or_copy_file_impl(
        input_path="assets/powah/guides/powah/book/generators/furnator.md",
        **_powah_kwargs(tmp_path, writes),
    )

    assert list(writes) == [
        "other/assets/powah/guides/powah/book/generators/furnator.md"
    ]


# ---------------------------------------------------------------------------
# 官方繁中優先：模組自帶 _zh_tw 時，英文原件不再送 LM
# ---------------------------------------------------------------------------


def test_official_zh_tw_is_kept_and_english_is_not_retranslated(tmp_path: Path):
    merge = tmp_path / "merge"
    book = merge / "other_output/assets/powah/guides/powah/book"
    (book / "_zh_tw/generators").mkdir(parents=True)
    (book / "generators").mkdir(parents=True)
    # 官方譯文含英文名詞（Powah、FE）也要整份保留，不能被當成待翻譯
    (book / "_zh_tw/generators/furnator.md").write_text(
        "# 燃燒發電機\n\n以 Powah 的 FE 供電。\n", "utf-8"
    )
    (book / "generators/furnator.md").write_text(
        "# Furnator\n\nGenerates FE from burnable items.\n", "utf-8"
    )
    (book / "generators/magmator.md").write_text(
        "# Magmator\n\nGenerates FE from lava and other hot fluids.\n", "utf-8"
    )
    (book / "index.md").write_text("# Powah\n\nAll about energy here.\n", "utf-8")

    records = {
        Path(r["relative_path"]).as_posix().split("book/")[1]: (
            r["status"],
            r["reason"],
        )
        for r in classify_other_output_assets(merge)
    }

    assert records["_zh_tw/generators/furnator.md"] == (
        "emitted",
        "official_translation_preserved",
    )
    assert records["generators/furnator.md"] == (
        "excluded",
        "english_text_has_official_translation",
    )
    assert records["generators/magmator.md"][0] == "pending"
    assert (
        (merge / "assets/powah/guides/powah/book/_zh_tw/generators/furnator.md")
        .read_text("utf-8")
        .startswith("# 燃燒發電機")
    )


# ---------------------------------------------------------------------------
# 資源包打包頁手動打包：與一鍵流水線用同一條原文語系規則
# ---------------------------------------------------------------------------


def test_manual_bundle_skips_source_locale_files(tmp_path: Path):
    guide = tmp_path / "src/assets/demo/guides/demo/guide"
    (guide / "_zh_tw/images").mkdir(parents=True)
    (guide / "_zh_tw/index.md").write_text("# 指南\n", "utf-8")
    (guide / "_zh_tw/images/a.png").write_bytes(b"tw")
    (guide / "_zh_cn/images").mkdir(parents=True)
    (guide / "_zh_cn/images/a.png").write_bytes(b"cn")
    text = tmp_path / "src/assets/tombstone/text/_zh_tw"
    text.mkdir(parents=True)
    (text / "coral_chant_1_en_us.txt").write_text("舊的錯誤路徑", "utf-8")
    output_zip = tmp_path / "pack.zip"

    updates = list(bundle_outputs_generator(str(tmp_path / "src"), str(output_zip)))

    with zipfile.ZipFile(output_zip) as zf:
        names = set(zf.namelist())
    assert "assets/demo/guides/demo/guide/_zh_tw/images/a.png" in names
    assert not any("zh_cn" in name or "en_us" in name for name in names)
    warnings = [u["log"] for u in updates if "語系的檔案" in str(u.get("log", ""))]
    assert len(warnings) == 1 and "略過 2 個" in warnings[0]
