"""巢狀 JAR 一般翻譯流程的回歸測試。"""

from __future__ import annotations

import io
import json
import re
import shutil
import zipfile
from pathlib import Path

from app.services_impl.pipelines.bundle_service import build_bundle_staging
from translation_tool.core.jar_processor import (
    _extract_from_jar,
    build_book_path_regex,
    build_lang_file_regex,
    extract_book_files_generator,
    extract_lang_files_generator,
)
from translation_tool.core.jar_processor_preview import _scan_single_jar_for_preview
from translation_tool.core.jar_sniff_extract import (
    extract_sniffed_book_files,
    extract_sniffed_from_jar,
)
from translation_tool.core.lang_merge_extracted_assets import merge_extracted_to_assets
from translation_tool.core.output_bundler import bundle_outputs_generator
from translation_tool.translation_db.scanner import ScanOptions, scan_jar
from translation_tool.utils.cancellation import TaskCancelled, cancel_scope
from translation_tool.utils.jar_browser import scan_jars

_LANG_PATTERN = r"assets/[^/]+/lang/en_us\.json$"


def _zip_bytes(entries: dict[str, bytes | str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            archive.writestr(
                name, value.encode("utf-8") if isinstance(value, str) else value
            )
    return buffer.getvalue()


def _outer_jar(
    path: Path,
    *,
    nested: dict[str, bytes] | None = None,
    direct: dict[str, bytes | str] | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in (direct or {}).items():
            archive.writestr(
                name, value.encode("utf-8") if isinstance(value, str) else value
            )
        for name, value in (nested or {}).items():
            archive.writestr(name, value)
    return path


def _sodium_like_inner(value: str = "General") -> bytes:
    return _zip_bytes(
        {
            "assets/sodium/lang/en_us.json": json.dumps(
                {
                    "sodium.options.pages.general": value,
                    "sodium.options.pages.video": "Video",
                }
            )
        }
    )


def _custom_book_inner(summary: str = "A nested book that needs translation.") -> bytes:
    return _zip_bytes(
        {
            "assets/demo/books/chapters/foods.json": json.dumps(
                {
                    "title": "demo.book.title",
                    "pages": [
                        {
                            "type": "standard",
                            "summary": summary,
                        }
                    ],
                }
            )
        }
    )


def test_nested_scan_preview_and_canonical_extraction(tmp_path: Path):
    jar = _outer_jar(
        tmp_path / "sodium-neoforge-0.8.13+mc1.21.1.jar",
        nested={"META-INF/jarjar/sodium-mod.jar": _sodium_like_inner()},
    )
    regex = build_lang_file_regex(codes=["en_us"])

    scanned = scan_jars(tmp_path, [regex.pattern], max_workers=1, jar_files=[jar])

    assert scanned[jar]["assets/sodium/lang/en_us.json"] is not None
    assert scanned.nested_jars[jar] == 1
    assert scanned.skipped_nested[jar] == 0
    assert scanned.entries[jar][0].member_chain == (
        "META-INF/jarjar/sodium-mod.jar",
        "assets/sodium/lang/en_us.json",
    )

    preview = _scan_single_jar_for_preview(str(jar), "lang", regex, None, None)
    assert preview["error"] is None
    assert preview["matched_files"] == ["assets/sodium/lang/en_us.json"]

    output = tmp_path / "lang_output"
    result = _extract_from_jar(str(jar), str(output), regex, scanned)
    assert result == {"status": "success", "extracted": 1, "skipped": 0}
    extracted = output / (
        "sodium-neoforge-0.8.13+mc1.21.1_extracted/assets/sodium/lang/en_us.json"
    )
    assert extracted.is_file()
    assert (
        json.loads(extracted.read_text(encoding="utf-8"))[
            "sodium.options.pages.general"
        ]
        == "General"
    )


def test_direct_extraction_without_pre_scan_includes_nested_entries(tmp_path: Path):
    """公開的直接抽取入口也必須使用 bounded nested walker。"""
    jar = _outer_jar(
        tmp_path / "direct-nested.jar",
        nested={"META-INF/jarjar/inner.jar": _sodium_like_inner()},
    )
    regex = re.compile(_LANG_PATTERN)
    output = tmp_path / "out"

    result = _extract_from_jar(str(jar), str(output), regex)

    assert result == {"status": "success", "extracted": 1, "skipped": 0}
    assert (output / "direct-nested_extracted/assets/sodium/lang/en_us.json").is_file()


def test_nested_outputs_are_isolated_for_same_named_outer_jars(tmp_path: Path):
    """不同來源的同名 outer JAR 不得互相覆蓋 nested 語言檔。"""
    mods = tmp_path / "mods"
    _outer_jar(
        mods / "client" / "example.jar",
        nested={"META-INF/jarjar/inner.jar": _sodium_like_inner("Client")},
    )
    _outer_jar(
        mods / "server" / "example.jar",
        nested={"META-INF/jarjar/inner.jar": _sodium_like_inner("Server")},
    )
    output = tmp_path / "lang_output"

    list(extract_lang_files_generator(str(mods), str(output), lang_codes=["en_us"]))

    extracted = sorted(output.glob("*_extracted/assets/sodium/lang/en_us.json"))
    assert len(extracted) == 2
    assert {
        json.loads(path.read_text(encoding="utf-8"))["sodium.options.pages.general"]
        for path in extracted
    } == {"Client", "Server"}
    assert len({path.parent.parent.parent.parent.name for path in extracted}) == 2


def test_nested_content_sniff_is_shared_by_preview_and_extraction(tmp_path: Path):
    jar = _outer_jar(
        tmp_path / "nested-sniff.jar",
        nested={"META-INF/jarjar/books.jar": _custom_book_inner()},
    )
    book_regex = build_book_path_regex(codes=["en_us"])
    member = "assets/demo/books/chapters/foods.json"

    db_scan = scan_jar(jar, ScanOptions("1.21.1"), dir_names=())
    assert db_scan.nested_jars == 1
    assert any(
        item.kind == "patchouli" and item.key.endswith("foods.json#pages[0].summary")
        for item in db_scan.items
    )

    preview = _scan_single_jar_for_preview(
        str(jar), "book", book_regex, book_path_regex=None, lang_regex=None
    )
    assert preview["error"] is None
    assert preview["matched_files"] == [member]

    output = tmp_path / "book_output"
    written, skipped = extract_sniffed_from_jar(
        str(jar), str(output), lambda name: bool(book_regex.search(name)), {"en_us"}
    )
    assert (written, skipped) == (1, 0)
    extracted = output / "nested-sniff_extracted" / member
    assert extracted.is_file()
    assert "nested book that needs translation" in extracted.read_text(encoding="utf-8")

    pipeline_output = tmp_path / "book_pipeline_output"
    list(
        extract_book_files_generator(
            str(tmp_path), str(pipeline_output), lang_codes=["en_us"]
        )
    )
    assert (pipeline_output / "nested-sniff_extracted" / member).is_file()


def test_nested_sniff_outputs_are_isolated_for_same_named_outer_jars(tmp_path: Path):
    """內容嗅探與名稱提取使用相同的 nested 來源隔離規則。"""
    first = _outer_jar(
        tmp_path / "client" / "guide.jar",
        nested={"META-INF/jarjar/inner.jar": _custom_book_inner("Client")},
    )
    second = _outer_jar(
        tmp_path / "server" / "guide.jar",
        nested={"META-INF/jarjar/inner.jar": _custom_book_inner("Server")},
    )
    output = tmp_path / "book_output"
    updates = list(
        extract_sniffed_book_files(
            [str(first), str(second)],
            str(output),
            lambda _name: False,
            lang_codes=["en_us"],
            skip_zh_cn=False,
        )
    )

    extracted = sorted(output.glob("*_extracted/assets/demo/books/chapters/foods.json"))
    assert len(extracted) == 2
    assert {"Client", "Server"} == {
        json.loads(path.read_text(encoding="utf-8"))["pages"][0]["summary"]
        for path in extracted
    }
    assert updates[-1]["log"].endswith("2 個手冊／指南檔")


def test_nested_lang_and_book_phases_share_source_root_for_same_named_jars(
    tmp_path: Path,
):
    """名稱提取與內容補掃必須給同一 outer JAR 相同的 wrapper 名稱。"""
    mods = tmp_path / "mods"
    for folder, value in (("client", "Client"), ("server", "Server")):
        _outer_jar(
            mods / "addons" / folder / "guide.jar",
            nested={
                "META-INF/jarjar/inner.jar": _zip_bytes(
                    {
                        "assets/demo/lang/en_us.json": json.dumps({"demo.key": value}),
                        "assets/demo/books/chapters/foods.json": json.dumps(
                            {
                                "title": "demo.book.title",
                                "pages": [{"summary": value}],
                            }
                        ),
                    }
                )
            },
        )

    output = tmp_path / "output"
    list(extract_lang_files_generator(str(mods), str(output), lang_codes=["en_us"]))
    list(extract_book_files_generator(str(mods), str(output), lang_codes=["en_us"]))

    lang_wrappers = {
        path.parents[3].name
        for path in output.glob("*_extracted/assets/demo/lang/en_us.json")
    }
    book_wrappers = {
        path.parents[4].name
        for path in output.glob("*_extracted/assets/demo/books/chapters/foods.json")
    }
    assert lang_wrappers == book_wrappers
    assert len(lang_wrappers) == 2


def test_nested_content_sniff_preview_reports_partial_nested_archive(
    tmp_path: Path,
):
    inner = _zip_bytes(
        {
            "assets/demo/books/chapters/foods.json": json.dumps(
                {"title": "demo.book.title", "pages": [{"summary": "Book"}]}
            ),
            "META-INF/jarjar/broken.jar": b"not a zip",
        }
    )
    jar = _outer_jar(
        tmp_path / "nested-sniff-partial.jar",
        nested={"META-INF/jarjar/books.jar": inner},
    )
    book_regex = build_book_path_regex(codes=["en_us"])

    preview = _scan_single_jar_for_preview(
        str(jar), "book", book_regex, book_path_regex=None, lang_regex=None
    )

    assert preview["error"] is None
    assert preview["matched_files"] == ["assets/demo/books/chapters/foods.json"]
    assert preview["partial"] is True
    assert preview["skipped_nested"] == 1


def test_nested_content_sniff_keeps_first_canonical_source(tmp_path: Path):
    jar = _outer_jar(
        tmp_path / "nested-sniff-collision.jar",
        nested={
            "META-INF/jarjar/a-books.jar": _custom_book_inner("From A"),
            "META-INF/jarjar/b-books.jar": _custom_book_inner("From B"),
        },
    )
    book_regex = build_book_path_regex(codes=["en_us"])
    member = "assets/demo/books/chapters/foods.json"

    preview = _scan_single_jar_for_preview(
        str(jar), "book", book_regex, book_path_regex=None, lang_regex=None
    )
    assert preview["matched_files"] == [member]

    output = tmp_path / "book_output"
    written, skipped = extract_sniffed_from_jar(
        str(jar), str(output), lambda name: bool(book_regex.search(name)), {"en_us"}
    )
    assert (written, skipped) == (1, 0)
    extracted = output / "nested-sniff-collision_extracted" / member
    assert "From A" in extracted.read_text(encoding="utf-8")


def test_cross_outer_direct_sniff_collision_uses_input_order(tmp_path: Path):
    """Book sniff 的 direct assets 也必須採用穩定的 first-wins 規則。"""
    member = "assets/demo/books/chapters/foods.json"
    first = _outer_jar(
        tmp_path / "first.jar",
        direct={
            member: json.dumps(
                {"title": "demo.book.title", "pages": [{"summary": "First"}]}
            )
        },
    )
    second = _outer_jar(
        tmp_path / "second.jar",
        direct={
            member: json.dumps(
                {"title": "demo.book.title", "pages": [{"summary": "Second"}]}
            )
        },
    )

    def extract_in_order(jars: list[Path], output: Path) -> str:
        updates = list(
            extract_sniffed_book_files(
                [str(path) for path in jars],
                str(output),
                lambda _name: False,
                lang_codes=["en_us"],
                skip_zh_cn=False,
            )
        )
        assert updates[-1]["log"].endswith("1 個手冊／指南檔")
        return json.loads((output / member).read_text(encoding="utf-8"))["pages"][0][
            "summary"
        ]

    assert extract_in_order([first, second], tmp_path / "first_output") == "First"
    assert extract_in_order([second, first], tmp_path / "second_output") == "Second"


def test_sniff_budget_exhaustion_is_reported_as_partial(monkeypatch, tmp_path: Path):
    from translation_tool.translation_db import sniff_groups

    monkeypatch.setattr(sniff_groups, "SNIFF_MAX_MEMBERS", 2)
    jar = _outer_jar(
        tmp_path / "sniff-budget.jar",
        direct={
            "assets/demo/books/chapters/alpha.json": json.dumps(
                {"pages": [{"type": "text", "summary": "Alpha text"}]}
            ),
            "assets/demo/books/chapters/beta.json": json.dumps(
                {"pages": [{"type": "text", "summary": "Beta text"}]}
            ),
        },
    )
    book_regex = build_book_path_regex(codes=["en_us"])

    preview = _scan_single_jar_for_preview(
        str(jar),
        "book",
        book_regex,
        book_path_regex=None,
        lang_regex=None,
    )

    assert preview["partial"] is True
    assert preview["sniff_incomplete"] is True
    assert preview["count"] == 1

    output = tmp_path / "book_output"
    updates = list(
        extract_book_files_generator(str(tmp_path), str(output), lang_codes=["en_us"])
    )
    assert any(
        update.get("level") == "warning" and "部分完成" in update["log"]
        for update in updates
    )


def test_nested_translation_result_reaches_stage2_staging_and_zip(tmp_path: Path):
    jar = _outer_jar(
        tmp_path / "sodium.jar",
        nested={"META-INF/jars/sodium-inner.jar": _sodium_like_inner()},
    )
    regex = re.compile(_LANG_PATTERN)
    output = tmp_path / "lang_output"
    scanned = scan_jars(tmp_path, [regex.pattern], max_workers=1, jar_files=[jar])
    _extract_from_jar(str(jar), str(output), regex, scanned)

    extracted = next(output.glob("*_extracted/assets/sodium/lang/en_us.json"))
    extracted.with_name("zh_tw.json").write_text(
        json.dumps(
            {
                "sodium.options.pages.general": "一般",
                "sodium.options.pages.video": "影片",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    merge_updates = list(merge_extracted_to_assets(output, use_translation_db=False))
    assert merge_updates[-1]["error"] is False
    translated = output / "assets/sodium/lang/zh_tw.json"
    assert (
        json.loads(translated.read_text(encoding="utf-8"))["sodium.options.pages.video"]
        == "影片"
    )

    staging = tmp_path / "staging"
    stats = build_bundle_staging([str(output)], str(staging))
    assert stats["written"] == 1
    staged = staging / "assets/sodium/lang/zh_tw.json"
    assert (
        json.loads(staged.read_text(encoding="utf-8"))["sodium.options.pages.general"]
        == "一般"
    )

    bundle_input = tmp_path / "bundle_input" / "root"
    shutil.copytree(staging, bundle_input, dirs_exist_ok=True)
    zip_path = tmp_path / "translated.zip"
    updates = list(
        bundle_outputs_generator(
            str(bundle_input.parent), str(zip_path), force_rebuild=True
        )
    )
    assert not any(update.get("error") for update in updates)
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        assert "assets/sodium/lang/zh_tw.json" in names
        assert not any(name.startswith("META-INF/jarjar/") for name in names)


def test_nested_corrupt_archive_is_skipped_without_hiding_good_resource(tmp_path: Path):
    jar = _outer_jar(
        tmp_path / "mixed.jar",
        nested={
            "META-INF/jarjar/good.jar": _sodium_like_inner(),
            "META-INF/jarjar/broken.jar": b"not a zip",
        },
    )
    result = scan_jars(tmp_path, [_LANG_PATTERN], max_workers=1, jar_files=[jar])

    assert "assets/sodium/lang/en_us.json" in result[jar]
    assert result.nested_jars[jar] == 1
    assert result.skipped_nested[jar] == 1
    assert result.partial_jars == {jar}

    preview = _scan_single_jar_for_preview(
        str(jar), "lang", re.compile(_LANG_PATTERN), None, None
    )
    assert preview["partial"] is True
    assert preview["skipped_nested"] == 1


def test_nested_archive_size_limit_is_applied(monkeypatch, tmp_path: Path):
    from translation_tool.utils import nested_archive

    monkeypatch.setattr(nested_archive, "MAX_FILE_BYTES", 16)
    jar = _outer_jar(
        tmp_path / "oversized.jar",
        nested={"META-INF/jarjar/large.jar": _sodium_like_inner()},
    )

    result = scan_jars(tmp_path, [_LANG_PATTERN], max_workers=1, jar_files=[jar])

    assert jar not in result
    assert result.nested_jars[jar] == 0
    assert result.skipped_nested[jar] == 1


def test_nested_archive_depth_limit_is_deterministic(tmp_path: Path):
    payload = _zip_bytes(
        {"assets/deep/lang/en_us.json": json.dumps({"deep.key": "deep"})}
    )
    for index in range(4):
        payload = _zip_bytes({f"META-INF/jarjar/level-{index}.jar": payload})
    jar = _outer_jar(
        tmp_path / "deep.jar", direct={"META-INF/jarjar/root.jar": payload}
    )

    result = scan_jars(tmp_path, [_LANG_PATTERN], max_workers=1, jar_files=[jar])

    # Four nested containers exist, but the shared walker intentionally visits
    # only depths 1..3.
    assert jar not in result
    assert result.nested_jars[jar] == 3
    assert result.truncated_nested[jar] == 1
    assert result.partial_jars == {jar}


def test_database_scan_reports_nested_depth_truncation(tmp_path: Path):
    payload = _zip_bytes(
        {"assets/deep/lang/en_us.json": json.dumps({"deep.key": "deep"})}
    )
    for index in range(4):
        payload = _zip_bytes({f"META-INF/jarjar/level-{index}.jar": payload})
    jar = _outer_jar(
        tmp_path / "deep-db.jar", direct={"META-INF/jarjar/root.jar": payload}
    )

    result = scan_jar(jar, ScanOptions("1.21.1"), dir_names=())

    assert result.error == ""
    assert result.truncated_nested == 1
    assert any("深度超過上限" in note for note in result.notes)


def test_nested_canonical_collision_keeps_first_source_and_logs(tmp_path: Path):
    jar = _outer_jar(
        tmp_path / "collision.jar",
        nested={
            "META-INF/jarjar/a.jar": _sodium_like_inner("From A"),
            "META-INF/jarjar/b.jar": _sodium_like_inner("From B"),
        },
    )
    regex = re.compile(_LANG_PATTERN)
    result = scan_jars(tmp_path, [regex.pattern], max_workers=1, jar_files=[jar])

    assert len(result.entries[jar]) == 1
    assert result.collision_notes[jar]
    assert result[jar]["assets/sodium/lang/en_us.json"]
    assert "From A" in result[jar]["assets/sodium/lang/en_us.json"]

    output = tmp_path / "out"
    _extract_from_jar(str(jar), str(output), regex, result)
    extracted = next(output.glob("*_extracted/assets/sodium/lang/en_us.json"))
    assert "From A" in extracted.read_text(encoding="utf-8")


def test_stage2_collision_source_order_is_deterministic(tmp_path: Path):
    output = tmp_path / "lang_output"
    for folder, value in (
        ("z-outer_extracted", "乙"),
        ("a-outer_extracted", "甲"),
    ):
        lang_dir = output / folder / "assets" / "shared" / "lang"
        lang_dir.mkdir(parents=True)
        (lang_dir / "en_us.json").write_text(
            json.dumps({"shared.key": "Shared"}), encoding="utf-8"
        )
        (lang_dir / "zh_tw.json").write_text(
            json.dumps({"shared.key": value}, ensure_ascii=False), encoding="utf-8"
        )

    updates = list(merge_extracted_to_assets(output, use_translation_db=False))

    assert updates[-1]["error"] is False
    translated = json.loads(
        (output / "assets/shared/lang/zh_tw.json").read_text(encoding="utf-8")
    )
    assert translated["shared.key"] == "甲"


def test_direct_outer_jar_path_is_unchanged(tmp_path: Path):
    jar = _outer_jar(
        tmp_path / "direct.jar",
        direct={"assets/direct/lang/en_us.json": '{"direct.key":"Direct"}'},
    )
    regex = re.compile(_LANG_PATTERN)
    result = scan_jars(tmp_path, [regex.pattern], max_workers=1, jar_files=[jar])
    output = tmp_path / "out"
    _extract_from_jar(str(jar), str(output), regex, result)

    assert (output / "assets/direct/lang/en_us.json").is_file()
    assert not list(output.glob("*_extracted/assets/direct/lang/en_us.json"))


def test_cross_jar_asset_collision_uses_input_order_not_worker_order(tmp_path: Path):
    first = _outer_jar(
        tmp_path / "first.jar",
        direct={"assets/shared/lang/en_us.json": '{"value":"first"}'},
    )
    second = _outer_jar(
        tmp_path / "second.jar",
        direct={"assets/shared/lang/en_us.json": '{"value":"second"}'},
    )
    regex = re.compile(_LANG_PATTERN)
    scanned = scan_jars(
        tmp_path,
        [regex.pattern],
        max_workers=2,
        jar_files=[first, second],
    )
    output = tmp_path / "out"

    # Deliberately extract in the opposite completion order.  The canonical
    # plan must still keep the first input JAR as the direct-assets owner.
    _extract_from_jar(str(second), str(output), regex, scanned)
    _extract_from_jar(str(first), str(output), regex, scanned)

    assert (
        json.loads(
            (output / "assets/shared/lang/en_us.json").read_text(encoding="utf-8")
        )["value"]
        == "first"
    )


def test_nested_walker_honors_cancellation_before_reading(tmp_path: Path):
    from translation_tool.utils.nested_archive import walk_nested_archives
    from translation_tool.utils.zip_safety import ZipReadBudget

    jar = _outer_jar(
        tmp_path / "cancel.jar",
        nested={"META-INF/jarjar/inner.jar": _sodium_like_inner()},
    )
    visited: list[int] = []

    with cancel_scope(lambda: True), zipfile.ZipFile(jar) as archive:
        try:
            walk_nested_archives(
                archive,
                jar.name,
                ZipReadBudget(label=jar.name),
                lambda *_args: visited.append(1),
                should_cancel=lambda: True,
            )
        except TaskCancelled:
            pass

    assert visited == []
