import re
import zipfile
from pathlib import Path

import pytest

from translation_tool.core import jar_processor
from translation_tool.core.jar_processor_extract import (
    extract_from_jar_impl,
    get_file_hash,
)


def test_get_file_hash_returns_sha256_hex():
    """get_file_hash 必須回傳 SHA-256 16 進位字串（64 字元）"""
    data = b"hello world"
    hash_str = get_file_hash(data)

    # SHA-256("hello world") = b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9
    assert isinstance(hash_str, str)
    assert len(hash_str) == 64
    assert (
        hash_str == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
    )


def test_get_file_hash_empty_data_returns_known_sha256():
    """空資料的 SHA-256 是已知的常數（e3b0c44...）"""
    hash_str = get_file_hash(b"")

    # SHA-256("") = e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
    assert (
        hash_str == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_extract_from_jar_impl_direct_includes_jar_process_extract_impl(tmp_path: Path):
    """extract_from_jar_impl 是 jar_processor_extract.py 的公開 API（不是 wrapper）

    既有 test_jar_processor_extract.py 測的是 _extract_from_jar (wrapper),
    但 extract_from_jar_impl 是實際實作,直接測一下避免 wrapper 跟 impl 偏離。

    Args:
        tmp_path: pytest fixture 提供暫存目錄
    """
    jar_path = tmp_path / "demo-1.0.0.jar"
    with zipfile.ZipFile(jar_path, "w") as zf:
        zf.writestr("assets/demo/lang/en_us.json", '{"a":"A"}')

    result = extract_from_jar_impl(
        str(jar_path),
        str(tmp_path / "out"),
        re.compile(
            r"(?:assets/([^/]+)/)?lang/(en_us|zh_cn|zh_tw)\.(json|lang)$", re.IGNORECASE
        ),
    )

    assert result == {"status": "success", "extracted": 1, "skipped": 0}
    assert (tmp_path / "out" / "assets" / "demo" / "lang" / "en_us.json").exists()


def test_extract_from_jar_writes_assets_to_stable_output_path(tmp_path: Path):
    jar_path = tmp_path / "demo-1.0.0.jar"
    with zipfile.ZipFile(jar_path, "w") as zf:
        zf.writestr("assets/demo/lang/en_us.json", '{"a":"A"}')

    result = jar_processor._extract_from_jar(
        str(jar_path),
        str(tmp_path / "out"),
        re.compile(
            r"(?:assets/([^/]+)/)?lang/(en_us|zh_cn|zh_tw)\.(json|lang)$", re.IGNORECASE
        ),
    )

    assert result == {"status": "success", "extracted": 1, "skipped": 0}
    assert (tmp_path / "out" / "assets" / "demo" / "lang" / "en_us.json").exists()


def test_extract_from_jar_writes_non_assets_under_extracted_folder(tmp_path: Path):
    jar_path = tmp_path / "demo-neoforge-1.0.0.jar"
    with zipfile.ZipFile(jar_path, "w") as zf:
        zf.writestr("lang/en_us.json", '{"a":"A"}')

    result = jar_processor._extract_from_jar(
        str(jar_path),
        str(tmp_path / "out"),
        re.compile(r"lang/(en_us|zh_cn|zh_tw)\.(json|lang)$", re.IGNORECASE),
    )

    assert result == {"status": "success", "extracted": 1, "skipped": 0}
    assert (tmp_path / "out" / "demo_extracted" / "lang" / "en_us.json").exists()


def test_corrupted_jar_is_counted_as_failure(tmp_path: Path):
    """損毀的 JAR 不可被靜默跳過：最終 stats 的 failures 與 log 都要反映。"""
    import re

    from translation_tool.core.jar_processor_extract import run_extraction_process_impl

    mods = tmp_path / "mods"
    mods.mkdir()
    good = mods / "good-1.0.jar"
    with zipfile.ZipFile(good, "w") as zf:
        zf.writestr("assets/good/lang/en_us.json", '{"a": "b"}')
    bad = mods / "broken-1.0.jar"
    bad.write_bytes(b"not a zip file at all")

    regex = re.compile(r"assets/[^/]+/lang/en_us\.json$")
    updates = list(
        run_extraction_process_impl(
            str(mods),
            str(tmp_path / "out"),
            regex,
            "Lang",
            find_jar_files_fn=lambda d: [str(good), str(bad)],
            extract_from_jar_fn=jar_processor._extract_from_jar,
        )
    )

    final = updates[-1]
    assert final["stats"]["failures"] == 1
    assert final["stats"]["success"] == 1
    assert "broken-1.0.jar" in final["log"]
    assert any(
        "[ERROR]" in (u.get("log") or "") and "broken-1.0.jar" in u["log"]
        for u in updates
    )


def test_extraction_uses_only_scan_eligible_jars(tmp_path: Path, monkeypatch):
    """預掃描後只對含目標內容的 JAR 建立 extraction 工作。"""
    import re

    from translation_tool.core.jar_processor_extract import run_extraction_process_impl
    from translation_tool.utils.jar_browser import ScanResults

    mods = tmp_path / "mods"
    mods.mkdir()
    jars = []
    for index in range(10):
        jar = mods / f"mod-{index}.jar"
        with zipfile.ZipFile(jar, "w") as zf:
            if index < 3:
                zf.writestr(f"assets/mod{index}/lang/en_us.json", "{}")
        jars.append(str(jar))

    extracted = []

    def fake_scan(**kwargs):
        result = ScanResults()
        for jar in kwargs["jar_files"][:3]:
            result[Path(jar)] = {"assets/mod/lang/en_us.json": "{}"}
        return result

    def fake_extract(jar, *_args):
        extracted.append(jar)
        return {"status": "success", "extracted": 1, "skipped": 0}

    monkeypatch.setattr("translation_tool.utils.jar_browser.scan_jars", fake_scan)
    updates = list(
        run_extraction_process_impl(
            str(mods),
            str(tmp_path / "out"),
            re.compile(r"assets/[^/]+/lang/en_us\.json$"),
            "Lang",
            find_jar_files_fn=lambda _directory: jars,
            extract_from_jar_fn=fake_extract,
        )
    )

    assert extracted == jars[:3]
    assert updates[-1]["total"] == 3
    assert updates[-1]["stats"]["scanned_jars"] == 10
    assert updates[-1]["stats"]["eligible_jars"] == 3


def test_extraction_without_eligible_jars_finishes_without_future(
    tmp_path, monkeypatch
):
    """沒有符合內容的 JAR 時不提交 extraction Future，仍正常完成。"""
    import re

    from translation_tool.core.jar_processor_extract import run_extraction_process_impl
    from translation_tool.utils.jar_browser import ScanResults

    mods = tmp_path / "mods"
    mods.mkdir()
    jar = mods / "empty.jar"
    with zipfile.ZipFile(jar, "w"):
        pass

    def fake_scan(**_kwargs):
        return ScanResults()

    def fail_if_called(*_args):
        raise AssertionError("沒有 eligible JAR 時不應呼叫 extraction")

    monkeypatch.setattr("translation_tool.utils.jar_browser.scan_jars", fake_scan)
    updates = list(
        run_extraction_process_impl(
            str(mods),
            str(tmp_path / "out"),
            re.compile(r"assets/[^/]+/lang/en_us\.json$"),
            "Lang",
            find_jar_files_fn=lambda _directory: [str(jar)],
            extract_from_jar_fn=fail_if_called,
        )
    )

    assert updates[-1]["progress"] == 1.0
    assert updates[-1]["total"] == 0
    assert updates[-1]["stats"]["failures"] == 0


def test_pre_scan_progress_has_phase_boundary_and_is_monotonic(tmp_path, monkeypatch):
    """預掃描至少產生 20% phase 邊界，且整體進度單調。"""
    import re

    from translation_tool.core.jar_processor_extract import run_extraction_process_impl
    from translation_tool.utils.jar_browser import ScanResults

    mods = tmp_path / "mods"
    mods.mkdir()
    jar = mods / "mod.jar"
    with zipfile.ZipFile(jar, "w"):
        pass

    def fake_scan(**_kwargs):
        return ScanResults()

    monkeypatch.setattr("translation_tool.utils.jar_browser.scan_jars", fake_scan)
    updates = list(
        run_extraction_process_impl(
            str(mods),
            str(tmp_path / "out"),
            re.compile(r"assets/[^/]+/lang/en_us\.json$"),
            "Lang",
            find_jar_files_fn=lambda _directory: [str(jar)],
            extract_from_jar_fn=lambda *_args: {
                "status": "success",
                "extracted": 0,
                "skipped": 0,
            },
        )
    )

    progress = [update["progress"] for update in updates if "progress" in update]
    assert progress == sorted(progress)
    assert 0.2 in progress
    assert progress[-1] == 1.0


def test_scan_failure_does_not_emit_completed_scan_or_reset_progress(
    tmp_path: Path, monkeypatch
):
    """預掃描例外時不應先宣稱完成，也不應把 progress 降回 0。"""
    import re

    from translation_tool.core.jar_processor_extract import run_extraction_process_impl

    mods = tmp_path / "mods"
    mods.mkdir()
    jars = [str(mods / f"mod-{index}.jar") for index in range(3)]

    def failing_scan(**kwargs):
        kwargs["processed_callback"](1, 3)
        raise RuntimeError("scan exploded")

    monkeypatch.setattr("translation_tool.utils.jar_browser.scan_jars", failing_scan)
    updates = list(
        run_extraction_process_impl(
            str(mods),
            str(tmp_path / "out"),
            re.compile(r"assets/[^/]+/lang/en_us\.json$"),
            "Lang",
            find_jar_files_fn=lambda _directory: jars,
            extract_from_jar_fn=lambda *_args: pytest.fail("extraction should not run"),
        )
    )

    progress = [update["progress"] for update in updates if "progress" in update]
    assert progress == sorted(progress)
    assert updates[-1]["error"] is True
    assert updates[-1]["current"] == 1
    assert "3/3" not in updates[-1]["log"]


def test_scan_skipped_target_preserves_warning_stats_without_rescan(
    tmp_path: Path, monkeypatch
):
    """matching 但因安全限制跳過的 JAR 仍保留 warning 統計。"""
    import re

    from translation_tool.core.jar_processor_extract import run_extraction_process_impl
    from translation_tool.utils.jar_browser import ScanResults

    mods = tmp_path / "mods"
    mods.mkdir()
    jar = mods / "large.jar"
    jar.write_bytes(b"placeholder")

    def fake_scan(**_kwargs):
        result = ScanResults()
        result.skipped_jars.add(Path(jar))
        return result

    monkeypatch.setattr("translation_tool.utils.jar_browser.scan_jars", fake_scan)
    updates = list(
        run_extraction_process_impl(
            str(mods),
            str(tmp_path / "out"),
            re.compile(r"assets/[^/]+/lang/en_us\.json$"),
            "Lang",
            find_jar_files_fn=lambda _directory: [str(jar)],
            extract_from_jar_fn=lambda *_args: pytest.fail(
                "skipped JAR should not rescan"
            ),
        )
    )

    assert updates[-1]["stats"]["eligible_jars"] == 0
    assert updates[-1]["stats"]["warnings"] == 1
    assert updates[-1]["stats"]["failures"] == 0
