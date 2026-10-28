"""跨入口維護契約：同一份 fixture 不得在不同流程得到不同資料。

這裡刻意使用測試內 fixture，而不是再新增一層 production abstraction。這些契約
固定住四個來源入口（ModDB、預覽、提取、LM）以及兩個輸出入口（獨立 bundler、
一鍵流水線）共同依賴的檔案身分、文字區塊與資源包邊界。
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from app.services_impl.pipelines.bundle_service import build_bundle_staging
from translation_tool.core.jar_processor import (
    build_book_path_regex,
    extract_dual_files_generator,
)
from translation_tool.core.jar_processor_preview import (
    preview_extraction_generator_impl,
)
from translation_tool.core.lm_translator_scan import (
    extract_items_parallel,
    scan_translatable_files,
)
from translation_tool.core.output_bundler import bundle_outputs_generator
from translation_tool.translation_db.scanner import ScanOptions, scan_jar
from translation_tool.translation_db.schema import KIND_GUIDE, KIND_LANG, KIND_PATCHOULI


def _zip_bytes(entries: dict[str, bytes | str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            archive.writestr(
                name,
                value.encode("utf-8") if isinstance(value, str) else value,
            )
    return buffer.getvalue()


def _standard_book() -> str:
    return json.dumps(
        {
            "name": "Getting Started",
            "pages": [
                {
                    "type": "patchouli:text",
                    "text": "Welcome to the demo manual.",
                }
            ],
        }
    )


def _neutral_book(summary: str) -> str:
    return json.dumps(
        {
            "pages": [
                {
                    "type": "standard",
                    "summary": summary,
                }
            ]
        }
    )


def _guide_text(title: str, body: str) -> str:
    return f"---\nnavigation:\n  title: {title}\n---\n\n# {title}\n\n{body}\n"


def _write_relative_text(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _contract_members() -> dict[str, str]:
    """同一份來源 fixture；JAR、直接目錄與輸出測試都由這組資料衍生。"""
    return {
        "assets/demo/lang/en_us.json": json.dumps(
            {
                "item.demo.widget": "Demo Widget",
                "gui.demo.message": "Press the button to continue",
            }
        ),
        "assets/demo/patchouli_books/manual/en_us/entries/intro.json": _standard_book(),
        "assets/demo/books/chapters/foods.json": _neutral_book(
            "This custom book explains the harvest."
        ),
        "assets/demo/books/en_us/guide.md": _guide_text(
            "Demo Guide", "This guide explains the demo system."
        ),
        "assets/demo/books/en_us/notes.txt": (
            "This text guide explains the demo system.\n"
        ),
        "assets/nested/lang/en_us.json": json.dumps(
            {"item.nested.widget": "Nested Widget"}
        ),
        "assets/nested/books/chapters/nested.json": _neutral_book(
            "This nested book explains the bundled component."
        ),
        "assets/nested/books/en_us/guide.md": _guide_text(
            "Nested Guide", "This nested guide explains the inner module."
        ),
    }


def _write_contract_jar(path: Path) -> Path:
    members = _contract_members()
    nested_members = {
        name: value
        for name, value in members.items()
        if name.startswith("assets/nested/")
    }
    outer_members = {
        name: value
        for name, value in members.items()
        if not name.startswith("assets/nested/")
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in outer_members.items():
            archive.writestr(name, value)
        archive.writestr(
            "META-INF/jarjar/contract-inner.jar",
            _zip_bytes(nested_members),
        )
    return path


def _write_contract_tree(root: Path) -> None:
    for relative, content in _contract_members().items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _relative_source(path: str | Path, root: Path) -> str:
    """把 nested extraction wrapper 還原成 JAR 的 canonical member 路徑。"""
    relative = Path(path).resolve().relative_to(root.resolve()).as_posix()
    parts = relative.split("/")
    if parts and parts[0].endswith("_extracted"):
        relative = "/".join(parts[1:])
    return relative


def _last_extraction_batch(root: Path) -> tuple[list[Path], list[dict]]:
    _content_files, _lang_files, files = scan_translatable_files(root)
    batches = list(
        extract_items_parallel(
            files=files,
            export_lang=False,
            work_thread=1,
        )
    )
    assert batches, "fixture 應至少產生一個可翻譯檔案批次"
    _file_cache, items = batches[-1]
    return files, items


def test_same_jar_fixture_is_consistent_across_db_preview_extraction_and_lm(
    tmp_path: Path,
):
    """同一 JAR 的 direct/nested lang、書籍與指南在四個入口不可分叉。"""
    mods = tmp_path / "mods"
    jar = _write_contract_jar(mods / "contract-1.0.jar")

    db_result = scan_jar(
        jar,
        ScanOptions(version="1.21.1"),
        dir_names=("patchouli_books", "book", "manual", "guidebook"),
    )
    assert not db_result.error
    db_identities = {(item.kind, item.mod_id, item.key) for item in db_result.items}
    assert (KIND_LANG, "demo", "item.demo.widget") in db_identities
    assert (KIND_LANG, "nested", "item.nested.widget") in db_identities
    assert any(
        kind == KIND_PATCHOULI
        and mod_id == "demo"
        and key.startswith("patchouli_books/manual/entries/intro.json#")
        for kind, mod_id, key in db_identities
    )
    assert any(
        kind == KIND_PATCHOULI
        and mod_id == "demo"
        and key.startswith("books/chapters/foods.json#")
        for kind, mod_id, key in db_identities
    )
    assert any(
        kind == KIND_GUIDE and mod_id == "demo" and key.startswith("books/guide.md#")
        for kind, mod_id, key in db_identities
    )
    assert any(
        kind == KIND_GUIDE and mod_id == "demo" and key.startswith("books/notes.txt#")
        for kind, mod_id, key in db_identities
    )
    assert any(
        kind == KIND_GUIDE and mod_id == "nested" and key.startswith("books/guide.md#")
        for kind, mod_id, key in db_identities
    )

    book_regex = build_book_path_regex(codes=["en_us"])
    preview_updates = list(
        preview_extraction_generator_impl(
            str(mods),
            "dual",
            find_jar_files_fn=lambda _directory: [str(jar)],
            book_path_regex=book_regex,
            lang_codes=["en_us"],
            skip_zh_cn=False,
        )
    )
    preview = preview_updates[-1]["result"]["preview_results"][0]
    assert set(preview["lang_files"]) == {
        "assets/demo/lang/en_us.json",
        "assets/nested/lang/en_us.json",
    }
    assert {
        "assets/demo/patchouli_books/manual/en_us/entries/intro.json",
        "assets/demo/books/chapters/foods.json",
        "assets/demo/books/en_us/guide.md",
        "assets/demo/books/en_us/notes.txt",
        "assets/nested/books/chapters/nested.json",
        "assets/nested/books/en_us/guide.md",
    } <= set(preview["book_files"])

    extracted = tmp_path / "extracted"
    extraction_updates = list(
        extract_dual_files_generator(
            str(mods),
            str(extracted),
            lang_codes=["en_us"],
        )
    )
    assert not any(update.get("error") for update in extraction_updates)
    expected_sources = set(_contract_members())
    actual_sources = {
        _relative_source(path, extracted)
        for path in extracted.rglob("*")
        if path.is_file() and path.suffix.lower() in {".json", ".md", ".txt"}
    }
    assert expected_sources <= actual_sources

    files, lm_items = _last_extraction_batch(extracted)
    lm_sources = {_relative_source(item["file"], extracted) for item in lm_items}
    assert expected_sources <= lm_sources
    assert {str(path.relative_to(extracted).as_posix()) for path in files} >= {
        source for source in expected_sources if not source.startswith("assets/nested/")
    }

    db_texts = {item.en_us for item in db_result.items}
    lm_texts = {item["source_text"] for item in lm_items}
    assert {
        "Demo Widget",
        "Press the button to continue",
        "Welcome to the demo manual.",
        "This custom book explains the harvest.",
        "This guide explains the demo system.",
        "This text guide explains the demo system.",
        "Nested Widget",
        "This nested book explains the bundled component.",
        "This nested guide explains the inner module.",
    } <= db_texts
    assert {
        "Demo Widget",
        "Press the button to continue",
        "Welcome to the demo manual.",
        "This custom book explains the harvest.",
        "This guide explains the demo system.",
        "This text guide explains the demo system.",
        "Nested Widget",
        "This nested book explains the bundled component.",
        "This nested guide explains the inner module.",
    } <= lm_texts


def test_book_and_markdown_fixture_preserves_block_identity_between_db_and_lm(
    tmp_path: Path,
):
    """書籍 JSON、Markdown 與 TXT 的文字與指南區塊身分要一致。"""
    source = tmp_path / "source"
    _write_contract_tree(source)
    jar = _write_contract_jar(tmp_path / "fixture.jar")

    db_items = scan_jar(
        jar,
        ScanOptions(version="1.21.1"),
        dir_names=("patchouli_books", "book", "manual", "guidebook"),
    ).items
    db_guides = {
        (item.mod_id, item.key, item.en_us)
        for item in db_items
        if item.kind == KIND_GUIDE
    }

    files, lm_items = _last_extraction_batch(source)
    assert any(path.name == "guide.md" for path in files)
    assert any(path.name == "notes.txt" for path in files)
    lm_guides = {
        (
            item["db_ident"][1],
            item["db_ident"][2],
            item["source_text"],
        )
        for item in lm_items
        if item.get("cache_type") == "md"
    }
    assert db_guides == lm_guides

    # JSON 書籍也必須至少由兩邊辨識到相同的來源與可翻譯文字；指南的精確
    # block ordinal 則由上面的 db_ident 等值比較固定下來。
    db_book_texts = {item.en_us for item in db_items if item.kind == KIND_PATCHOULI}
    lm_book_texts = {
        item["source_text"]
        for item in lm_items
        if item.get("cache_type") == "patchouli"
    }
    assert {
        "Welcome to the demo manual.",
        "This custom book explains the harvest.",
    } <= db_book_texts
    assert {
        "Welcome to the demo manual.",
        "This custom book explains the harvest.",
    } <= lm_book_texts


def _write_output_contract_fixture(root: Path) -> None:
    files = {
        "assets/demo/lang/zh_tw.json": json.dumps(
            {"item.demo.widget": "示範物品"}, ensure_ascii=False
        ),
        "assets/demo/patchouli_books/manual/zh_tw/entries/intro.json": _standard_book(),
        "assets/demo/books/chapters/foods.json": _neutral_book("收穫手冊"),
        "assets/demo/guides/_zh_tw/demo/guide/machines/batching.md": "# 舊版路徑\n",
        "data/demo/advancements/story/start.json": json.dumps(
            {"display": {"title": {"text": "Data Pack title"}}}
        ),
        "other_output/assets/demo/guide/pending.md": "Pending English guide\n",
        "assets/demo/pack.mcmeta": "{}",
        "pack_1.mcmeta": "{}",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _zip_snapshot(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def test_standalone_and_pipeline_bundles_share_resource_pack_data_pack_boundary(
    tmp_path: Path,
):
    """獨立 bundler 與流水線 staging 都只能把合法 assets 放進 Resource Pack。"""
    source = tmp_path / "source"
    _write_output_contract_fixture(source)

    standalone_zip = tmp_path / "standalone.zip"
    standalone_updates = list(
        bundle_outputs_generator(
            str(source),
            str(standalone_zip),
            description="contract",
            pack_format=34,
            force_rebuild=True,
        )
    )
    assert not any(update.get("error") for update in standalone_updates)

    staging = tmp_path / "staging"
    stats = build_bundle_staging(
        [str(source)],
        str(staging),
        content_manifest_path=str(tmp_path / "content-manifest.json"),
    )
    # data/、other_output/ 與根目錄的 pack_1.mcmeta 都不能進入 Resource Pack。
    assert stats["excluded"] == 3
    assert any("Advancement" in warning for warning in stats["warnings"])
    assert any("other_output" in warning for warning in stats["warnings"])

    pipeline_zip = tmp_path / "pipeline.zip"
    pipeline_updates = list(
        bundle_outputs_generator(
            str(staging),
            str(pipeline_zip),
            description="contract",
            pack_format=34,
            force_rebuild=True,
        )
    )
    assert not any(update.get("error") for update in pipeline_updates)

    standalone_snapshot = _zip_snapshot(standalone_zip)
    pipeline_snapshot = _zip_snapshot(pipeline_zip)
    standalone_names = set(standalone_snapshot)
    pipeline_names = set(pipeline_snapshot)
    assert standalone_names == pipeline_names
    assert standalone_snapshot == pipeline_snapshot
    assert "pack.mcmeta" in standalone_names
    assert "assets/demo/lang/zh_tw.json" in standalone_names
    assert (
        "assets/demo/patchouli_books/manual/zh_tw/entries/intro.json"
        in standalone_names
    )
    assert "assets/demo/books/chapters/foods.json" in standalone_names
    assert (
        "assets/demo/guides/demo/guide/_zh_tw/machines/batching.md" in standalone_names
    )
    assert (
        "assets/demo/guides/_zh_tw/demo/guide/machines/batching.md"
        not in standalone_names
    )
    assert all(
        name == "pack.mcmeta" or name == "pack.png" or name.startswith("assets/")
        for name in standalone_names
    )
    assert not any(name.startswith("data/") for name in standalone_names)
    assert not any(name.startswith("other_output/") for name in standalone_names)
    assert "assets/demo/pack.mcmeta" not in standalone_names
    assert "pack_1.mcmeta" not in standalone_names

    manifest = json.loads(
        (tmp_path / "translation_output_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["resource_pack_boundary"] == "assets_only"
    assert any(
        record["status"] == "excluded"
        and record["reason"] == "resource_pack_excludes_advancement_data"
        for record in manifest["records"]
    )


@pytest.mark.parametrize("canonical_first", [False, True])
def test_staging_and_standalone_bundling_share_guideme_canonical_precedence(
    tmp_path: Path, canonical_first: bool
):
    """GuideME aliases cannot change the winner between pipeline and direct bundling."""
    canonical_root = tmp_path / "canonical"
    legacy_root = tmp_path / "legacy"
    canonical_rel = "assets/demo/guides/demo/guide/_zh_tw/machines/batching.md"
    legacy_rel = "assets/demo/guides/_zh_tw/demo/guide/machines/batching.md"
    _write_relative_text(canonical_root, canonical_rel, "# canonical\n")
    _write_relative_text(legacy_root, legacy_rel, "# legacy\n")
    sources = (
        [canonical_root, legacy_root]
        if canonical_first
        else [legacy_root, canonical_root]
    )
    source_paths = [str(path) for path in sources]

    standalone_zip = tmp_path / "standalone-guideme.zip"
    standalone_updates = list(
        bundle_outputs_generator(
            source_paths[0],
            str(standalone_zip),
            description="contract",
            pack_format=34,
            extra_folders=source_paths[1:],
            force_rebuild=True,
        )
    )
    assert not any(update.get("error") for update in standalone_updates)

    staging = tmp_path / "guideme-staging"
    build_bundle_staging(source_paths, str(staging))
    pipeline_zip = tmp_path / "pipeline-guideme.zip"
    pipeline_updates = list(
        bundle_outputs_generator(
            str(staging),
            str(pipeline_zip),
            description="contract",
            pack_format=34,
            force_rebuild=True,
        )
    )
    assert not any(update.get("error") for update in pipeline_updates)

    standalone_snapshot = _zip_snapshot(standalone_zip)
    pipeline_snapshot = _zip_snapshot(pipeline_zip)
    assert standalone_snapshot == pipeline_snapshot
    assert (
        standalone_snapshot[
            "assets/demo/guides/demo/guide/_zh_tw/machines/batching.md"
        ].replace(b"\r\n", b"\n")
        == b"# canonical\n"
    )
    assert (
        "assets/demo/guides/_zh_tw/demo/guide/machines/batching.md"
        not in standalone_snapshot
    )

    manifest = json.loads(
        (tmp_path / "translation_output_manifest.json").read_text(encoding="utf-8")
    )
    assert any(
        record["status"] == "excluded"
        and record["reason"] == "legacy_guideme_alias_replaced_by_canonical"
        for record in manifest["records"]
    )
