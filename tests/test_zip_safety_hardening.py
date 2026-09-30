"""tests/test_zip_safety_hardening.py

PR #105 安全性收尾：
- safe_join 對 symlink / junction 逃逸的防護（解析後的真實位置也必須在 root 內）
- ZipReadBudget：同一 archive 的累計解壓縮量 / 讀取成員數上限
- 整合層回歸：icon_reader、icon_preview、icon_index、lang_merge_content_copy
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import threading
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from translation_tool.core.jar_processor_extract import extract_from_jar_impl
from translation_tool.core.lang_merge_content_copy import (
    process_content_or_copy_file_impl,
)
from translation_tool.core.lang_merge_io import ZipReader
from translation_tool.utils import zip_safety
from translation_tool.utils.jar_browser import _scan_single_jar
from translation_tool.utils.zip_safety import (
    ArchiveBudgetError,
    UnsafePathError,
    ZipReadBudget,
    ZipSizeError,
    read_limited,
    safe_join,
)


def _make_zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(zipfile.ZipInfo(name), data)
    return path


@pytest.fixture(scope="module")
def symlinks_supported(tmp_path_factory) -> bool:
    """能否建立目錄 symlink（Windows 一般帳號沒有權限時為 False）。"""
    base = tmp_path_factory.mktemp("symcheck")
    (base / "t").mkdir()
    try:
        (base / "l").symlink_to(base / "t", target_is_directory=True)
    except (OSError, NotImplementedError):
        return False
    return True


@pytest.fixture
def need_symlinks(symlinks_supported):
    if not symlinks_supported:
        pytest.skip("此環境無法建立 symlink")


# ---------------------------------------------------------------------------
# safe_join：symlink / junction
# ---------------------------------------------------------------------------


def test_safe_join_rejects_symlink_parent_escape(tmp_path, need_symlinks):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnsafePathError):
        safe_join(root, "link", "evil.txt")


def test_safe_join_rejects_symlink_escape_with_missing_subdirs(tmp_path, need_symlinks):
    """root/assets/external -> outside，成員 assets/external/sub/payload.json（sub 尚不存在）。"""
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    (root / "assets").mkdir(parents=True)
    outside.mkdir()
    (root / "assets" / "external").symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnsafePathError):
        safe_join(root, "assets", "external", "sub", "payload.json")


def test_safe_join_rejects_existing_symlink_file_target(tmp_path, need_symlinks):
    """最終檔案本身是指向 root 外的 symlink：覆寫它會寫到 root 外。"""
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    victim = outside / "victim.txt"
    victim.write_text("keep", encoding="utf-8")
    (root / "file.txt").symlink_to(victim)

    with pytest.raises(UnsafePathError):
        safe_join(root, "file.txt")


def test_safe_join_rejects_dangling_symlink_parent(tmp_path, need_symlinks):
    """指向 root 外「尚不存在」位置的 symlink 也要擋（寫入時會在 root 外建立內容）。"""
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "dangling").symlink_to(outside / "not_yet", target_is_directory=True)

    with pytest.raises(UnsafePathError):
        safe_join(root, "dangling", "x.json")


def test_safe_join_allows_symlink_that_stays_inside_root(tmp_path, need_symlinks):
    root = tmp_path / "root"
    (root / "real").mkdir(parents=True)
    (root / "alias").symlink_to(root / "real", target_is_directory=True)

    result = safe_join(root, "alias", "ok.json")

    assert result == os.path.abspath(root / "alias" / "ok.json")


def test_safe_join_allows_root_reached_through_symlink(tmp_path, need_symlinks):
    """root 本身經由 symlink 存取（例如 /data -> /mnt/disk）不可被誤判為逃逸。"""
    real_root = tmp_path / "real_root"
    real_root.mkdir()
    link_root = tmp_path / "link_root"
    link_root.symlink_to(real_root, target_is_directory=True)

    result = safe_join(link_root, "assets", "m", "a.json")

    assert result == os.path.abspath(link_root / "assets" / "m" / "a.json")


def test_safe_join_allows_nonexistent_nested_path(tmp_path):
    """上層目錄與檔案都還沒建立時，正常路徑仍可通過。"""
    root = tmp_path / "root"
    root.mkdir()

    result = safe_join(root, "assets", "新模組", "lang", "zh_tw.json")

    assert result == os.path.abspath(root / "assets" / "新模組" / "lang" / "zh_tw.json")


def test_safe_join_allows_root_that_does_not_exist_yet(tmp_path):
    result = safe_join(tmp_path / "later", "a", "b.txt")
    assert result == os.path.abspath(tmp_path / "later" / "a" / "b.txt")


@pytest.mark.skipif(sys.platform != "win32", reason="junction 只存在於 Windows")
def test_safe_join_rejects_windows_junction_escape(tmp_path):
    """Windows junction（reparse point）：不需要管理員權限即可建立。"""
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(root / "junc"), str(outside)],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        pytest.skip("無法建立 junction")

    with pytest.raises(UnsafePathError):
        safe_join(root, "junc", "evil.txt")


def test_extract_from_jar_does_not_write_through_symlink(tmp_path, need_symlinks):
    """整合：輸出目錄內既有 symlink 指向外部時，JAR 成員不可被寫到外部。"""
    out = tmp_path / "out"
    outside = tmp_path / "outside"
    (out / "assets").mkdir(parents=True)
    outside.mkdir()
    (out / "assets" / "external").symlink_to(outside, target_is_directory=True)
    jar = _make_zip(
        tmp_path / "evil.jar",
        {
            "assets/external/payload.json": b'{"pwn": 1}',
            "assets/mod/lang/en_us.json": b"{}",
        },
    )

    import re

    result = extract_from_jar_impl(str(jar), str(out), re.compile(r"\.json$"))

    assert not list(outside.rglob("*"))
    assert (out / "assets" / "mod" / "lang" / "en_us.json").exists()
    assert result["extracted"] == 1
    assert result["skipped"] == 1


# ---------------------------------------------------------------------------
# ZipReadBudget：累計上限
# ---------------------------------------------------------------------------


def _many_members_zip(n: int, size: int) -> zipfile.ZipFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i in range(n):
            zf.writestr(f"m{i}.json", b"A" * size)
    buf.seek(0)
    return zipfile.ZipFile(buf)


def test_archive_budget_rejects_many_individually_valid_members():
    """每個成員都低於單檔上限，但累計超過整包預算。"""
    zf = _many_members_zip(30, 1000)
    budget = ZipReadBudget(max_bytes=10_000, max_members=1000)

    results = []
    with pytest.raises(ArchiveBudgetError):
        for i in range(30):
            results.append(
                read_limited(zf, f"m{i}.json", max_bytes=2000, budget=budget)
            )

    # 單一成員本身合法（證明不是單檔上限擋的），累計到 10 個之後才被拒絕
    assert len(results) == 10
    assert all(len(r) == 1000 for r in results)


def test_archive_budget_rejects_when_member_count_exceeded():
    zf = _many_members_zip(10, 10)
    budget = ZipReadBudget(max_bytes=10**9, max_members=5)

    for i in range(5):
        read_limited(zf, f"m{i}.json", budget=budget)
    with pytest.raises(ArchiveBudgetError, match="成員數"):
        read_limited(zf, "m5.json", budget=budget)


def test_archive_budget_counts_actual_bytes_when_header_lies():
    """header 宣告很小、實際串流很大：累計預算仍要以實際位元組計。"""

    class _Stream(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _FakeZip:
        def open(self, _info):
            return _Stream(b"Z" * 50_000)

    info = zipfile.ZipInfo("lie.bin")
    info.file_size = 10
    budget = ZipReadBudget(max_bytes=20_000, max_members=10)

    with pytest.raises(ArchiveBudgetError):
        read_limited(_FakeZip(), info, max_bytes=10**9, budget=budget)  # type: ignore[arg-type]


def test_archive_budget_is_subclass_of_zip_size_error_and_stays_exhausted():
    zf = _many_members_zip(5, 1000)
    budget = ZipReadBudget(max_bytes=1500, max_members=100)

    read_limited(zf, "m0.json", budget=budget)
    with pytest.raises(ZipSizeError):  # ArchiveBudgetError 是 ZipSizeError 子類
        read_limited(zf, "m1.json", budget=budget)
    assert budget.exhausted
    with pytest.raises(ArchiveBudgetError):  # 用盡後持續拒絕
        read_limited(zf, "m2.json", budget=budget)


def test_archive_budget_logs_warning_once():
    zf = _many_members_zip(5, 1000)
    budget = ZipReadBudget(max_bytes=1500, max_members=100, label="evil.jar")

    with patch.object(zip_safety, "log_warning") as warn:
        read_limited(zf, "m0.json", budget=budget)
        for i in range(1, 5):
            with pytest.raises(ArchiveBudgetError):
                read_limited(zf, f"m{i}.json", budget=budget)

    assert warn.call_count == 1
    assert "evil.jar" in warn.call_args.args[0]


def test_per_file_limit_error_is_not_archive_budget_error():
    """單檔過大與整包累計超限要能區分（呼叫端處理方式不同）。"""
    zf = _many_members_zip(1, 5000)
    budget = ZipReadBudget()

    with pytest.raises(ZipSizeError) as exc:
        read_limited(zf, "m0.json", max_bytes=1000, budget=budget)

    assert not isinstance(exc.value, ArchiveBudgetError)
    assert budget.used_members == 0  # 被單檔上限擋下的成員不佔用成員數


def test_archive_budget_is_shared_across_threads():
    zf = _many_members_zip(80, 1000)
    budget = ZipReadBudget(max_bytes=30_000, max_members=10_000)
    ok: list[int] = []
    rejected: list[int] = []
    lock = threading.Lock()

    def worker(start: int):
        for i in range(start, start + 10):
            try:
                read_limited(zf, f"m{i}.json", budget=budget)
            except ArchiveBudgetError:
                with lock:
                    rejected.append(i)
            else:
                with lock:
                    ok.append(i)

    threads = [threading.Thread(target=worker, args=(n * 10,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(ok) <= 30  # 累計 30_000 bytes / 1000 bytes
    assert rejected
    # 超過上限當下，每條執行緒最多各有一筆進行中的讀取
    assert budget.used_bytes <= 30_000 + 8 * 1000


def test_zip_reader_shares_budget_across_instances():
    """語言合併每個任務各建一個 ZipReader：共用同一個 budget 才有累計效果。"""
    zf = _many_members_zip(10, 1000)
    budget = ZipReadBudget(max_bytes=3500, max_members=100)
    readers = [ZipReader(zf, budget) for _ in range(10)]

    reads = 0
    with pytest.raises(ArchiveBudgetError):
        for i, reader in enumerate(readers):
            reader.read_bytes(f"m{i}.json")
            reads += 1
    assert reads == 3  # 第 4 次讀取會使累計超過 3500 bytes
    assert budget.used_bytes == 3000


def test_scan_single_jar_discards_archive_when_budget_exceeded(tmp_path, monkeypatch):
    jar = _make_zip(
        tmp_path / "bomb.jar",
        {f"assets/m/lang/l{i}.json": b"{}" + b" " * 1000 for i in range(20)},
    )
    monkeypatch.setattr(
        "translation_tool.utils.jar_browser.ZipReadBudget",
        lambda label="": ZipReadBudget(max_bytes=5000, max_members=1000, label=label),
    )

    path, content = _scan_single_jar(jar, [r"assets/m/lang/.*\.json$"])

    assert path == jar
    assert content == {}  # 只讀到一部分會讓後續誤以為內容完整 → 整包捨棄


def test_extract_from_jar_reports_error_when_budget_exceeded(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    jar = _make_zip(
        tmp_path / "bomb.jar",
        {f"assets/m/lang/l{i}.json": b"{}" + b" " * 1000 for i in range(20)},
    )
    monkeypatch.setattr(
        "translation_tool.core.jar_processor_extract.ZipReadBudget",
        lambda label="": ZipReadBudget(max_bytes=5000, max_members=1000, label=label),
    )

    import re

    result = extract_from_jar_impl(str(jar), str(out), re.compile(r"\.json$"))

    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# 整合：icon_reader / icon_preview / icon_index
# ---------------------------------------------------------------------------

_BIG_ICON = b"\x00" * (3 * 1024 * 1024)  # > MAX_ICON_BYTES（2MB），壓縮後很小
_BIG_TEXT = b" " * (11 * 1024 * 1024)  # > MAX_TEXT_BYTES（10MB）


def test_read_icon_bytes_returns_none_for_oversized_png(tmp_path):
    from app.icon_reader import read_icon_bytes

    jar = _make_zip(
        tmp_path / "m.jar",
        {"assets/m/big.png": _BIG_ICON, "assets/m/ok.png": b"\x89PNG-small"},
    )

    assert read_icon_bytes(jar, "assets/m/big.png") is None
    assert read_icon_bytes(jar, "assets/m/ok.png") == b"\x89PNG-small"


@pytest.fixture
def icon_preview(monkeypatch):
    from app.views import icon_preview_view as mod

    # 不碰磁碟上的 model index 快取
    monkeypatch.setattr(mod, "_load_model_index_from_cache", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_save_model_index_to_cache", lambda *a, **k: None)
    return mod


def test_extract_jar_icon_skips_oversized_fallback_icon(tmp_path, icon_preview):
    jar = _make_zip(tmp_path / "m.jar", {"assets/m/icon.png": _BIG_ICON})
    cache_root = tmp_path / "cache"

    result = icon_preview._extract_jar_icon(jar, "m", cache_root, "item.m.x")

    assert result is None
    assert not cache_root.exists() or not list(cache_root.rglob("*.png"))


def test_extract_jar_icon_skips_oversized_logo_texture(tmp_path, icon_preview):
    jar = _make_zip(tmp_path / "m.jar", {"assets/m/textures/logo.png": _BIG_ICON})
    cache_root = tmp_path / "cache"

    result = icon_preview._extract_jar_icon(jar, "m", cache_root, "item.m.x")

    assert result is None
    assert not cache_root.exists() or not list(cache_root.rglob("*.png"))


def test_extract_jar_icon_oversized_model_falls_back_to_valid_icon(
    tmp_path, icon_preview
):
    """model JSON 過大 → 略過該 model，不崩潰，仍走後續 fallback 取得合法 icon。"""
    jar = _make_zip(
        tmp_path / "m.jar",
        {
            "assets/m/models/item/a.json": _BIG_TEXT,
            "assets/m/icon.png": b"\x89PNG-ok",
        },
    )
    cache_root = tmp_path / "cache"

    result = icon_preview._extract_jar_icon(jar, "m", cache_root, "item.m.a")

    assert result is not None
    assert result.read_bytes() == b"\x89PNG-ok"


def test_iter_lang_entries_skips_oversized_lang_file(tmp_path):
    """icon_index：過大的 lang 檔被略過，不使整個索引建置中止。"""
    from app.icon_index import _iter_entries_from_lang_files

    jar = _make_zip(
        tmp_path / "m.jar",
        {
            "assets/m/lang/a.lang": _BIG_TEXT,
            "assets/m/lang/b.lang": b"item.m.x=Foo\n",
        },
    )

    with zipfile.ZipFile(jar) as zf:
        entries = list(_iter_entries_from_lang_files(zf))

    assert entries == [("item.m.x", "Foo")]


def test_process_single_jar_survives_oversized_lang_and_returns_empty(
    tmp_path, icon_preview
):
    from app.icon_index import _process_single_jar

    jar = _make_zip(tmp_path / "m.jar", {"assets/m/lang/en_us.lang": _BIG_TEXT})

    assert _process_single_jar((jar, "m")) == {}


def test_process_single_jar_keeps_partial_index_when_budget_exceeded(
    tmp_path, icon_preview, monkeypatch
):
    """整包累計超限：保留已解析的部分，不丟例外、不中止。"""
    jar = _make_zip(
        tmp_path / "m.jar",
        {
            "assets/m/lang/en_us.lang": b"item.m.a=A\nitem.m.b=B\nitem.m.c=C\n",
            "assets/m/models/item/a.json": b'{"textures": {"layer0": "m:item/a"}}',
            "assets/m/models/item/b.json": b'{"textures": {"layer0": "m:item/b"}}',
            "assets/m/models/item/c.json": b'{"textures": {"layer0": "m:item/c"}}',
            "assets/m/textures/item/a.png": b"a",
            "assets/m/textures/item/b.png": b"b",
            "assets/m/textures/item/c.png": b"c",
        },
    )
    # lang 讀 1 次 + 第一個 key 的 model 讀 1 次後用盡（成員數上限 2）
    monkeypatch.setattr(
        ZipReadBudget,
        "for_icon_scan",
        classmethod(lambda cls, label="": cls(10**9, 2, label)),
    )
    from app.icon_index import _process_single_jar

    result = _process_single_jar((jar, "m"))

    assert list(result) == ["item.m.a"]


# ---------------------------------------------------------------------------
# 整合：lang_merge_content_copy 輸出路徑
# ---------------------------------------------------------------------------


class _FakeReader:
    def read_text(self, _p):
        return "hello"

    def read_bytes(self, _p):
        return b"hello"

    def list_all(self):
        return []

    def copy_to(self, _rel, target):
        os.makedirs(os.path.dirname(target), exist_ok=True)
        Path(target).write_bytes(b"x")


def _copy(input_path: str, output_dir: Path, other_dir: Path):
    import json

    return process_content_or_copy_file_impl(
        _FakeReader(),
        input_path,
        [],
        str(output_dir),
        all_files_cache=[],
        wrapper_prefix=None,
        load_config_fn=dict,
        recursive_translate_dict_fn=lambda text, _rules: text,
        get_text_processor_fn=lambda _p: None,
        write_bytes_atomic_fn=lambda p, d: Path(p).write_bytes(d),
        write_text_atomic_fn=lambda p, t: Path(p).write_text(t, encoding="utf-8"),
        quarantine_copy_fn=lambda *a, **k: None,
        normalize_patchouli_book_root_fn=lambda p: p,
        patch_localized_content_json_fn=lambda *a, **k: {},
        json_module=json,
        other_output_dir=str(other_dir),
    )


def test_content_copy_normal_path_still_written(tmp_path):
    out, other = tmp_path / "out", tmp_path / "other"
    out.mkdir()
    other.mkdir()

    result = _copy("docs/readme.txt", out, other)

    assert result["success"] is True
    assert (other / "docs" / "readme.txt").exists()


def test_content_copy_rejects_traversal_path(tmp_path):
    out, other = tmp_path / "out", tmp_path / "other"
    out.mkdir()
    other.mkdir()

    result = _copy("../../escape/readme.txt", out, other)

    assert result == {"success": False, "error": True}
    assert not [p for p in tmp_path.rglob("readme.txt")]


def test_content_copy_rejects_symlink_escape(tmp_path, need_symlinks):
    out, other = tmp_path / "out", tmp_path / "other"
    outside = tmp_path / "outside"
    out.mkdir()
    other.mkdir()
    outside.mkdir()
    (other / "docs").symlink_to(outside, target_is_directory=True)

    result = _copy("docs/readme.txt", out, other)

    assert result == {"success": False, "error": True}
    assert not list(outside.rglob("*"))


# ---------------------------------------------------------------------------
# 整合：lang_merge_pipeline 輸出路徑 / icon 快取檔名
# ---------------------------------------------------------------------------


class _LangReader:
    """回傳固定 lang JSON 的 reader（zh_cn 有內容，會產生輸出）。"""

    def read_text(self, _p):
        return '{"item.m.a": "測試"}'

    def read_json(self, _p):
        return {"item.m.a": "測試"}

    def read_bytes(self, _p):
        return b'{"item.m.a": "\\u6e2c\\u8a66"}'

    def list_all(self):
        return []


def _merge_one(paths: dict[str, str], out: Path, pending: Path):
    from translation_tool.core.lang_merge_pipeline import _process_single_mod

    return _process_single_mod(
        _LangReader(),
        paths,
        [],
        str(out),
        str(pending),
        None,
        all_files_cache=[],
        wrapper_prefix=None,
    )


def test_lang_merge_normal_mod_is_written(tmp_path):
    out, pending = tmp_path / "out", tmp_path / "pending"
    out.mkdir()
    pending.mkdir()

    result = _merge_one({"zh_cn": "assets/m/lang/zh_cn.json"}, out, pending)

    assert result["success"] is True
    assert list((out / "assets" / "m" / "lang").glob("zh_tw.*"))


def test_lang_merge_rejects_traversal_in_zip_member_path(tmp_path):
    """ZIP 成員路徑含 .. 時，zh_tw 輸出不可寫到 output_dir 之外。"""
    out, pending = tmp_path / "out", tmp_path / "pending"
    out.mkdir()
    pending.mkdir()

    result = _merge_one({"zh_cn": "assets/../../escaped/lang/zh_cn.json"}, out, pending)

    assert result == {"success": False, "error": True}
    assert not [p for p in tmp_path.rglob("zh_tw.*")]
    assert not (tmp_path / "escaped").exists()


def test_lang_merge_rejects_symlink_escape(tmp_path, need_symlinks):
    out, pending = tmp_path / "out", tmp_path / "pending"
    outside = tmp_path / "outside"
    out.mkdir()
    pending.mkdir()
    outside.mkdir()
    (out / "assets").symlink_to(outside, target_is_directory=True)

    result = _merge_one({"zh_cn": "assets/m/lang/zh_cn.json"}, out, pending)

    assert result == {"success": False, "error": True}
    assert not list(outside.rglob("*"))


def test_icon_cache_file_stays_inside_cache_root(tmp_path):
    from app.views import icon_preview_view as mod

    root = tmp_path / "cache"
    path = mod._icon_cache_file(root, "m", Path("x/mod-1.0.jar"), "item.m.a")

    assert path.parent == root
    assert path.name.startswith("m_mod-1.0_")


def test_icon_cache_file_rejects_escaping_modid(tmp_path):
    from app.views import icon_preview_view as mod

    root = tmp_path / "cache"

    with pytest.raises(UnsafePathError):
        mod._icon_cache_file(root, "../../evil", Path("x/mod.jar"), "item.m.a")


# ---------------------------------------------------------------------------
# 正式 extraction pipeline：預掃描 + 提取共用同一個 JAR 的累計預算
# ---------------------------------------------------------------------------

_LANG_REGEX = re.compile(r"assets/[^/]+/lang/.*\.json$")


def _lang_jar(
    path: Path, text: int, binary: int, size: int = 1000, prefix: str = ""
) -> Path:
    """text 個可解碼的文字成員（掃描時直接保存內容）+ binary 個無法 UTF-8 解碼的成員
    （掃描時解碼失敗記為 None，提取階段必須重新讀取）。每個成員解壓後都是 size bytes。
    prefix 用來讓不同 JAR 的輸出檔名不同（相同路徑且內容相同會被提取流程視為「跳過」）。"""
    entries: dict[str, bytes] = {}
    for i in range(text):
        entries[f"assets/m/lang/{prefix}t{i}.json"] = b"x" * size
    for i in range(binary):
        entries[f"assets/m/lang/{prefix}b{i}.json"] = b"\xff" * size
    return _make_zip(path, entries)


def _track_budgets(monkeypatch, max_bytes: int, max_members: int = 10_000):
    """把掃描與提取兩個模組建立的 ZipReadBudget 換成小預算並記錄建立了幾份。"""
    created: list[ZipReadBudget] = []

    def factory(label: str = "") -> ZipReadBudget:
        budget = ZipReadBudget(
            max_bytes=max_bytes, max_members=max_members, label=label
        )
        created.append(budget)
        return budget

    monkeypatch.setattr("translation_tool.utils.jar_browser.ZipReadBudget", factory)
    monkeypatch.setattr(
        "translation_tool.core.jar_processor_extract.ZipReadBudget", factory
    )
    return created


def _run_pipeline(mods: Path, out: Path, jars: list[Path]) -> list[dict]:
    """走正式流程：run_extraction_process_impl → scan_jars（預掃描）→ extract。"""
    from translation_tool.core import jar_processor
    from translation_tool.core.jar_processor_extract import (
        run_extraction_process_impl,
    )

    return list(
        run_extraction_process_impl(
            str(mods),
            str(out),
            _LANG_REGEX,
            "Lang",
            find_jar_files_fn=lambda _d: [str(j) for j in jars],
            extract_from_jar_fn=jar_processor._extract_from_jar,
        )
    )


@pytest.fixture
def mods_dir(tmp_path) -> Path:
    mods = tmp_path / "mods"
    mods.mkdir()
    return mods


def test_extraction_pipeline_shares_budget_between_prescan_and_extract(
    tmp_path, mods_dir, monkeypatch
):
    """預掃描 10KB（< 12KB）、提取階段重讀 4KB（< 12KB），但合計 14KB > 12KB。

    同一個 JAR 必須因累計超限失敗；若預掃描與提取各拿一份新預算就會錯誤地通過。
    """
    _track_budgets(monkeypatch, max_bytes=12_000)
    jar = _lang_jar(mods_dir / "bomb-1.0.jar", text=6, binary=4)
    out = tmp_path / "out"

    updates = _run_pipeline(mods_dir, out, [jar])

    final = updates[-1]
    assert final["stats"]["failures"] == 1
    assert final["stats"]["success"] == 0
    assert "bomb-1.0.jar" in final["log"]
    # 提取中途被中止：不是所有成員都被寫出
    assert len(list(out.rglob("*.json"))) < 10


def test_extraction_pipeline_uses_one_budget_per_jar(tmp_path, mods_dir, monkeypatch):
    """所有權：預掃描建立、提取沿用，同一個 JAR 只有一份預算。"""
    created = _track_budgets(monkeypatch, max_bytes=10**9)
    jar = _lang_jar(mods_dir / "ok-1.0.jar", text=3, binary=2)

    _run_pipeline(mods_dir, tmp_path / "out", [jar])

    assert len(created) == 1
    # 掃描讀 5 個成員；提取階段只重讀掃描時無法解碼的 2 個二進位成員
    assert created[0].used_members == 5 + 2
    assert created[0].used_bytes == (5 + 2) * 1000


def test_extraction_pipeline_does_not_recharge_cached_scan_results(
    tmp_path, mods_dir, monkeypatch
):
    """掃描時已讀取並保存的文字內容，提取階段直接使用，不重複計入預算。"""
    created = _track_budgets(monkeypatch, max_bytes=10**9)
    jar = _lang_jar(mods_dir / "text-1.0.jar", text=6, binary=0)
    out = tmp_path / "out"

    updates = _run_pipeline(mods_dir, out, [jar])

    assert updates[-1]["stats"]["success"] == 6
    assert len(list(out.rglob("*.json"))) == 6
    assert created[0].used_members == 6
    assert created[0].used_bytes == 6000


def test_extraction_pipeline_exhausted_budget_keeps_rejecting_same_jar(
    tmp_path, mods_dir, monkeypatch
):
    """預掃描就已超限 → 提取階段不可拿到新預算再讀一輪，而是沿用已用盡的預算持續拒絕。"""
    created = _track_budgets(monkeypatch, max_bytes=5000)
    jar = _lang_jar(mods_dir / "bomb-1.0.jar", text=20, binary=0)

    updates = _run_pipeline(mods_dir, tmp_path / "out", [jar])

    assert updates[-1]["stats"]["failures"] == 1
    assert len(created) == 1
    assert created[0].exhausted
    assert created[0].used_bytes == 5000  # 提取階段沒有再消耗任何預算


def test_extraction_pipeline_budget_is_not_shared_between_jars(
    tmp_path, mods_dir, monkeypatch
):
    """一個 JAR 超限不影響其他 JAR；預算也不會被錯誤地跨 JAR 共用。"""
    created = _track_budgets(monkeypatch, max_bytes=12_000)
    bomb = _lang_jar(mods_dir / "bomb-1.0.jar", text=6, binary=4)
    good = _lang_jar(mods_dir / "good-1.0.jar", text=2, binary=0, prefix="g")
    out = tmp_path / "out"

    updates = _run_pipeline(mods_dir, out, [bomb, good])

    final = updates[-1]
    assert final["stats"]["failures"] == 1
    assert final["stats"]["success"] == 2  # good-1.0.jar 的 2 個檔案
    assert "bomb-1.0.jar" in final["log"]
    assert "good-1.0.jar" not in final["log"].split("無法提取的 JAR")[-1]
    assert len(created) == 2
    assert sorted(b.label for b in created) == ["bomb-1.0.jar", "good-1.0.jar"]


def test_scan_jars_attaches_one_budget_per_jar_with_multiple_workers(tmp_path):
    from translation_tool.utils.jar_browser import ScanResults, scan_jars

    jars = [
        _lang_jar(tmp_path / f"m{i}-1.0.jar", text=i + 1, binary=0) for i in range(6)
    ]
    empty = _make_zip(tmp_path / "nomatch-1.0.jar", {"readme.txt": b"hi"})

    results = scan_jars(tmp_path, [r"assets/m/lang/.*\.json$"], max_workers=4)

    assert isinstance(results, ScanResults)
    assert isinstance(results, dict)
    assert set(results.budgets) == {*jars, empty}  # 包含沒有符合成員的 JAR
    for i, jar in enumerate(jars):
        assert results.budgets[jar].used_members == i + 1  # 每個 worker 只用自己的預算
        assert len(results[jar]) == i + 1
    assert results.budgets[empty].used_members == 0
    assert empty not in results  # 既有契約：沒有內容的 JAR 不在結果內
    assert len({id(b) for b in results.budgets.values()}) == len(results.budgets)


def test_scan_results_compares_equal_to_plain_dict(tmp_path):
    from translation_tool.utils.jar_browser import scan_jars

    jar = _lang_jar(tmp_path / "m-1.0.jar", text=1, binary=0)

    results = scan_jars(tmp_path, [r"assets/m/lang/.*\.json$"])

    assert results == {jar: {"assets/m/lang/t0.json": "x" * 1000}}


# ---------------------------------------------------------------------------
# _extract_jar_icon：model 與 fallback 讀取共用同一份 icon scan 預算
# ---------------------------------------------------------------------------


def _icon_budget(monkeypatch, max_members: int):
    monkeypatch.setattr(
        ZipReadBudget,
        "for_icon_scan",
        classmethod(lambda cls, label="": cls(10**9, max_members, label)),
    )


_MODEL_MISSING_TEXTURE = (
    b'{"textures": {"layer0": "m:item/zzz"}}'  # 貼圖不存在 → 走 fallback
)


@pytest.mark.parametrize(("max_members", "expect_icon"), [(1, False), (2, True)])
def test_extract_jar_icon_fabric_fallback_shares_budget(
    tmp_path, icon_preview, monkeypatch, max_members, expect_icon
):
    """model 讀 1 次；Fabric icon fallback 是第 2 次讀取，預算不足時不可寫入快取。"""
    _icon_budget(monkeypatch, max_members)
    jar = _make_zip(
        tmp_path / "m.jar",
        {
            "assets/m/models/item/a.json": _MODEL_MISSING_TEXTURE,
            "assets/m/icon.png": b"\x89PNG-fabric",
        },
    )
    cache_root = tmp_path / "cache"

    result = icon_preview._extract_jar_icon(jar, "m", cache_root, "item.m.a")

    if expect_icon:
        assert result is not None
        assert result.read_bytes() == b"\x89PNG-fabric"
    else:
        assert result is None
        assert not cache_root.exists() or not list(cache_root.rglob("*"))


@pytest.mark.parametrize(("max_members", "expect_icon"), [(2, False), (3, True)])
def test_extract_jar_icon_neoforge_toml_fallback_shares_budget(
    tmp_path, icon_preview, monkeypatch, max_members, expect_icon
):
    """model(1) + neoforge.mods.toml(2) + logoFile PNG(3)：兩個 fallback 讀取都計入預算。"""
    _icon_budget(monkeypatch, max_members)
    jar = _make_zip(
        tmp_path / "m.jar",
        {
            "assets/m/models/item/a.json": _MODEL_MISSING_TEXTURE,
            "META-INF/neoforge.mods.toml": b'logoFile = "logo.png"\n',
            "logo.png": b"\x89PNG-neo",
        },
    )
    cache_root = tmp_path / "cache"

    result = icon_preview._extract_jar_icon(jar, "m", cache_root, "item.m.a")

    if expect_icon:
        assert result is not None
        assert result.read_bytes() == b"\x89PNG-neo"
    else:
        assert result is None
        assert not cache_root.exists() or not list(cache_root.rglob("*"))


def test_extract_jar_icon_toml_read_itself_is_charged(
    tmp_path, icon_preview, monkeypatch
):
    """預算只夠 model 讀取時，TOML 讀取本身就被拒絕（不會繼續讀 logoFile）。"""
    _icon_budget(monkeypatch, 1)
    jar = _make_zip(
        tmp_path / "m.jar",
        {
            "assets/m/models/item/a.json": _MODEL_MISSING_TEXTURE,
            "META-INF/neoforge.mods.toml": b'logoFile = "logo.png"\n',
            "logo.png": b"\x89PNG-neo",
        },
    )

    result = icon_preview._extract_jar_icon(jar, "m", tmp_path / "cache", "item.m.a")

    assert result is None


@pytest.mark.parametrize(("max_members", "expect_icon"), [(1, False), (2, True)])
def test_extract_jar_icon_model_resolved_png_read_shares_budget(
    tmp_path, icon_preview, monkeypatch, max_members, expect_icon
):
    """model 解析出貼圖後讀取 PNG 是第 2 次讀取，同樣計入同一份預算。"""
    _icon_budget(monkeypatch, max_members)
    jar = _make_zip(
        tmp_path / "m.jar",
        {
            "assets/m/models/item/a.json": b'{"textures": {"layer0": "m:item/a"}}',
            "assets/m/textures/item/a.png": b"\x89PNG-model",
        },
    )
    cache_root = tmp_path / "cache"

    result = icon_preview._extract_jar_icon(jar, "m", cache_root, "item.m.a")

    if expect_icon:
        assert result is not None
        assert result.read_bytes() == b"\x89PNG-model"
    else:
        assert result is None
        assert not cache_root.exists() or not list(cache_root.rglob("*"))
