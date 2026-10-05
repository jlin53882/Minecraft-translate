"""build_bundle_staging 增量更新（#158）。

核心契約：增量結果必須與「刪除後完整重建」的舊行為相同；
來源沒變時不重寫，來源變動／刪除時只動受影響的輸出檔。
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from app.services_impl.pipelines import bundle_service
from app.services_impl.pipelines.bundle_service import build_bundle_staging


def _reference_full_rebuild(sources: list[str], staging_dir: str) -> dict:
    """舊版實作（先 rmtree 再整批重建），作為增量結果的對照基準。"""
    if os.path.isdir(staging_dir):
        shutil.rmtree(staging_dir)
    os.makedirs(staging_dir, exist_ok=True)
    copied = merged = 0
    for source in sources:
        content_root = os.path.join(source, "assets")
        if not os.path.isdir(content_root):
            continue
        for root, dirs, files in os.walk(content_root):
            dirs[:] = [d for d in dirs if d not in {"待翻譯", "待翻譯整理需翻譯"}]
            rel_root = os.path.relpath(root, source)
            for name in files:
                src = os.path.join(root, name)
                dst = os.path.join(staging_dir, rel_root, name)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if name.lower().endswith(".json") and os.path.exists(dst):
                    base = bundle_service._read_json_dict(dst)
                    extra = bundle_service._read_json_dict(src)
                    if base is not None and extra is not None:
                        base.update(extra)
                        with open(dst, "w", encoding="utf-8") as f:
                            json.dump(base, f, ensure_ascii=False, indent=2)
                        merged += 1
                        continue
                shutil.copy2(src, dst)
                copied += 1
    return {"copied": copied, "merged": merged}


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _put(path: Path, content: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_bytes(content)
    return path


def _lang(source: Path, modid: str, data: dict) -> Path:
    return _put(
        source / "assets" / modid / "lang" / "zh_tw.json",
        json.dumps(data, ensure_ascii=False),
    )


@pytest.fixture
def sources(tmp_path):
    """三個來源（優先序低→高），含重疊的 JSON、單一來源檔、略過資料夾與非 assets 檔。"""
    low, mid, high = tmp_path / "low", tmp_path / "mid", tmp_path / "high"
    _lang(low, "a", {"k1": "low1", "k2": "low2"})
    _lang(mid, "a", {"k2": "mid2", "k3": "mid3"})
    _lang(high, "a", {"k3": "high3"})
    _lang(low, "only_low", {"x": "只在低優先序"})
    _put(high / "assets" / "b" / "textures" / "icon.png", b"\x89PNG-high")
    _put(low / "assets" / "b" / "textures" / "icon.png", b"\x89PNG-low")
    _put(mid / "assets" / "b" / "patchouli_books" / "x.json", '{"b": 1}')
    _put(low / "assets" / "待翻譯" / "skip.json", "{}")
    _put(low / "assets" / "待翻譯整理需翻譯" / "skip2.json", "{}")
    _put(low / "translation_map.json", "{}")  # assets/ 以外：不得進 staging
    return [str(low), str(mid), str(high)]


class TestEquivalenceWithFullRebuild:
    def test_first_run_matches_full_rebuild(self, tmp_path, sources):
        inc, ref = tmp_path / "inc", tmp_path / "ref"
        stats = build_bundle_staging(sources, str(inc))
        ref_stats = _reference_full_rebuild(sources, str(ref))

        assert _snapshot(inc) == _snapshot(ref)
        assert stats["copied"] == ref_stats["copied"]
        assert stats["merged"] == ref_stats["merged"]
        assert stats["written"] > 0
        assert stats["unchanged"] == 0 and stats["removed"] == 0

    def test_merge_priority_and_skipped_content(self, tmp_path, sources):
        out = tmp_path / "out"
        build_bundle_staging(sources, str(out))
        merged = json.loads((out / "assets/a/lang/zh_tw.json").read_text("utf-8"))
        assert merged == {"k1": "low1", "k2": "mid2", "k3": "high3"}
        assert (out / "assets/b/textures/icon.png").read_bytes() == b"\x89PNG-high"
        assert not (out / "translation_map.json").exists()
        assert not (out / "assets" / "待翻譯").exists()
        assert not (out / "assets" / "待翻譯整理需翻譯").exists()

    def test_bom_and_non_dict_json_follow_old_semantics(self, tmp_path):
        low, high = tmp_path / "low", tmp_path / "high"
        _put(low / "assets/m/lang/bom.json", b"\xef\xbb\xbf" + b'{"a": 1}')
        _put(high / "assets/m/lang/bom.json", '{"b": 2}')
        _put(low / "assets/m/lang/list.json", '{"a": 1}')
        _put(high / "assets/m/lang/list.json", "[1, 2]")  # 非 dict：整個覆蓋
        _put(low / "assets/m/lang/broken.json", '{"a": 1}')
        _put(high / "assets/m/lang/broken.json", "{not json")  # 解析失敗：整個覆蓋
        srcs = [str(low), str(high)]

        inc, ref = tmp_path / "inc", tmp_path / "ref"
        stats = build_bundle_staging(srcs, str(inc))
        ref_stats = _reference_full_rebuild(srcs, str(ref))

        assert _snapshot(inc) == _snapshot(ref)
        assert (stats["copied"], stats["merged"]) == (
            ref_stats["copied"],
            ref_stats["merged"],
        )

    def test_three_way_chain_with_non_dict_in_the_middle(self, tmp_path):
        a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
        _put(a / "assets/m/x.json", '{"a": 1}')
        _put(b / "assets/m/x.json", "[]")
        _put(c / "assets/m/x.json", '{"c": 3}')
        srcs = [str(a), str(b), str(c)]
        inc, ref = tmp_path / "inc", tmp_path / "ref"
        build_bundle_staging(srcs, str(inc))
        _reference_full_rebuild(srcs, str(ref))
        assert _snapshot(inc) == _snapshot(ref)

    def test_missing_sources_are_skipped(self, tmp_path, sources):
        srcs = [str(tmp_path / "nope"), *sources, str(tmp_path / "nope2")]
        inc, ref = tmp_path / "inc", tmp_path / "ref"
        build_bundle_staging(srcs, str(inc))
        _reference_full_rebuild(srcs, str(ref))
        assert _snapshot(inc) == _snapshot(ref)

    def test_no_sources_gives_empty_staging(self, tmp_path):
        out = tmp_path / "out"
        stats = build_bundle_staging([str(tmp_path / "nope")], str(out))
        assert out.is_dir()
        assert (stats["copied"], stats["merged"]) == (0, 0)
        assert _snapshot(out) == {}


class TestIncrementalBehaviour:
    def test_second_run_with_same_sources_writes_nothing(
        self, tmp_path, sources, monkeypatch
    ):
        out = tmp_path / "out"
        first = build_bundle_staging(sources, str(out))
        before = _snapshot(out)

        writes: list[str] = []
        monkeypatch.setattr(bundle_service, "_copy_file", lambda s, d: writes.append(d))
        monkeypatch.setattr(
            bundle_service, "_write_bytes", lambda d, b: writes.append(d)
        )
        second = build_bundle_staging(sources, str(out))

        assert writes == []
        assert _snapshot(out) == before
        assert second["written"] == 0
        assert second["unchanged"] == first["written"]
        assert second["removed"] == 0
        # copied / merged 仍代表處理的來源檔數，呼叫端靠它判斷「有沒有可打包內容」
        assert (second["copied"], second["merged"]) == (
            first["copied"],
            first["merged"],
        )

    def test_staging_dir_is_not_deleted(self, tmp_path, sources, monkeypatch):
        out = tmp_path / "out"
        build_bundle_staging(sources, str(out))

        def boom(*_a, **_k):
            raise AssertionError("不應整體刪除 staging")

        monkeypatch.setattr(bundle_service.shutil, "rmtree", boom)
        build_bundle_staging(sources, str(out))

    def test_changing_one_source_file_rewrites_only_that_output(
        self, tmp_path, sources, monkeypatch
    ):
        out = tmp_path / "out"
        build_bundle_staging(sources, str(out))
        _put(
            Path(sources[0]) / "assets/only_low/lang/zh_tw.json",
            json.dumps({"x": "改過了"}, ensure_ascii=False),
        )

        writes: list[str] = []
        real_write = bundle_service._write_bytes
        real_copy = bundle_service._copy_file
        monkeypatch.setattr(
            bundle_service,
            "_write_bytes",
            lambda d, b: (writes.append(d), real_write(d, b)),
        )
        monkeypatch.setattr(
            bundle_service,
            "_copy_file",
            lambda s, d: (writes.append(d), real_copy(s, d)),
        )
        stats = build_bundle_staging(sources, str(out))

        assert [Path(w).relative_to(out).as_posix() for w in writes] == [
            "assets/only_low/lang/zh_tw.json"
        ]
        assert stats["written"] == 1
        ref = tmp_path / "ref"
        _reference_full_rebuild(sources, str(ref))
        assert _snapshot(out) == _snapshot(ref)

    def test_same_size_different_content_is_detected(self, tmp_path):
        src = tmp_path / "src"
        f = _put(src / "assets/m/data.bin", b"AAAA")
        out = tmp_path / "out"
        build_bundle_staging([str(src)], str(out))

        f.write_bytes(b"BBBB")  # 大小相同；mtime 也強制設成和 staging 內舊檔不同
        staged = out / "assets/m/data.bin"
        os.utime(f, ns=(1_000_000_000, 1_000_000_000))
        os.utime(staged, ns=(2_000_000_000, 2_000_000_000))
        stats = build_bundle_staging([str(src)], str(out))

        assert staged.read_bytes() == b"BBBB"
        assert stats["written"] == 1

    def test_same_content_different_mtime_is_not_rewritten(self, tmp_path, monkeypatch):
        src = tmp_path / "src"
        f = _put(src / "assets/m/data.bin", b"AAAA")
        out = tmp_path / "out"
        build_bundle_staging([str(src)], str(out))
        os.utime(f, ns=(3_000_000_000, 3_000_000_000))  # 只有 mtime 變了

        monkeypatch.setattr(
            bundle_service,
            "_copy_file",
            lambda s, d: pytest.fail("內容相同不應重寫"),
        )
        stats = build_bundle_staging([str(src)], str(out))
        assert stats["written"] == 0 and stats["unchanged"] == 1

    def test_removing_a_source_file_removes_orphans_and_empty_dirs(
        self, tmp_path, sources
    ):
        out = tmp_path / "out"
        build_bundle_staging(sources, str(out))
        (Path(sources[0]) / "assets/only_low/lang/zh_tw.json").unlink()

        stats = build_bundle_staging(sources, str(out))

        assert stats["removed"] == 1
        assert not (out / "assets/only_low").exists()  # 空資料夾也一併清掉
        ref = tmp_path / "ref"
        _reference_full_rebuild(sources, str(ref))
        assert _snapshot(out) == _snapshot(ref)

    def test_removing_the_highest_priority_source_reverts_merge(
        self, tmp_path, sources
    ):
        out = tmp_path / "out"
        build_bundle_staging(sources, str(out))
        lower = sources[:2]  # 拿掉優先序最高的來源

        build_bundle_staging(lower, str(out))

        merged = json.loads((out / "assets/a/lang/zh_tw.json").read_text("utf-8"))
        assert merged == {"k1": "low1", "k2": "mid2", "k3": "mid3"}
        assert (out / "assets/b/textures/icon.png").read_bytes() == b"\x89PNG-low"
        ref = tmp_path / "ref"
        _reference_full_rebuild(lower, str(ref))
        assert _snapshot(out) == _snapshot(ref)

    def test_stale_files_outside_assets_are_removed(self, tmp_path, sources):
        out = tmp_path / "out"
        build_bundle_staging(sources, str(out))
        _put(out / "leftover.txt", "舊版殘留")
        _put(out / "manifest-like" / "cache.json", "{}")
        _put(out / "assets" / "ghost" / "lang" / "zh_tw.json", "{}")

        stats = build_bundle_staging(sources, str(out))

        assert stats["removed"] == 3
        ref = tmp_path / "ref"
        _reference_full_rebuild(sources, str(ref))
        assert _snapshot(out) == _snapshot(ref)

    def test_file_directory_type_conflict_with_old_staging(self, tmp_path):
        src = tmp_path / "src"
        _put(src / "assets/m/dir/inner.txt", "inner")
        _put(src / "assets/m/file", "plain")
        out = tmp_path / "out"
        # 舊 staging：dir 是檔案、file 是資料夾
        _put(out / "assets/m/dir", "old file")
        _put(out / "assets/m/file/leftover.txt", "old dir content")

        build_bundle_staging([str(src)], str(out))

        ref = tmp_path / "ref"
        _reference_full_rebuild([str(src)], str(ref))
        assert _snapshot(out) == _snapshot(ref)

    def test_incremental_result_equals_rebuild_after_mixed_changes(
        self, tmp_path, sources
    ):
        out = tmp_path / "out"
        build_bundle_staging(sources, str(out))

        _lang(Path(sources[2]), "a", {"k3": "high3-new", "k4": "high4"})  # 改
        _lang(Path(sources[1]), "newmod", {"n": "新增"})  # 增
        (Path(sources[0]) / "assets/b/textures/icon.png").unlink()  # 刪
        build_bundle_staging(sources, str(out))

        ref = tmp_path / "ref"
        _reference_full_rebuild(sources, str(ref))
        assert _snapshot(out) == _snapshot(ref)

    def test_interrupted_run_converges_on_next_run(
        self, tmp_path, sources, monkeypatch
    ):
        out = tmp_path / "out"
        real_copy = bundle_service._copy_file
        calls = {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("simulated crash")
            real_copy(src, dst)

        monkeypatch.setattr(bundle_service, "_copy_file", flaky)
        with pytest.raises(OSError):
            build_bundle_staging(sources, str(out))
        monkeypatch.setattr(bundle_service, "_copy_file", real_copy)

        build_bundle_staging(sources, str(out))

        ref = tmp_path / "ref"
        _reference_full_rebuild(sources, str(ref))
        assert _snapshot(out) == _snapshot(ref)


class TestPipelineStepStillUsesStats:
    """_step_bundle 以 copied / merged 判斷有沒有內容；第二次（全沿用）不能被當成空。"""

    def test_unchanged_run_still_reports_content(self, tmp_path, sources):
        out = tmp_path / "out"
        build_bundle_staging(sources, str(out))
        second = build_bundle_staging(sources, str(out))
        assert second["copied"] or second["merged"]


class TestContentIdentityIsNeverInferredFromMtime:
    """review P2：大小與 mtime 都相同但內容不同時，仍必須更新 staging。"""

    def test_same_size_same_mtime_different_bytes_is_rewritten(self, tmp_path):
        src = tmp_path / "src"
        f = _put(src / "assets/m/data.bin", b"AAAA")
        out = tmp_path / "out"
        build_bundle_staging([str(src)], str(out))
        staged = out / "assets/m/data.bin"

        f.write_bytes(b"BBBB")  # 大小相同
        os.utime(f, ns=(5_000_000_000, 5_000_000_000))
        os.utime(staged, ns=(5_000_000_000, 5_000_000_000))  # mtime 也相同
        assert os.stat(f).st_size == os.stat(staged).st_size
        assert os.stat(f).st_mtime_ns == os.stat(staged).st_mtime_ns

        stats = build_bundle_staging([str(src)], str(out))

        assert staged.read_bytes() == b"BBBB", "不得沿用過期內容"
        assert stats["written"] == 1 and stats["unchanged"] == 0
        ref = tmp_path / "ref"
        _reference_full_rebuild([str(src)], str(ref))
        assert _snapshot(out) == _snapshot(ref)

    def test_difference_in_a_late_chunk_is_detected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bundle_service, "_COMPARE_CHUNK", 8)
        src = tmp_path / "src"
        f = _put(src / "assets/m/big.bin", b"x" * 100)
        out = tmp_path / "out"
        build_bundle_staging([str(src)], str(out))
        staged = out / "assets/m/big.bin"

        f.write_bytes(b"x" * 99 + b"y")  # 只有最後一個位元組不同
        os.utime(f, ns=(7_000_000_000, 7_000_000_000))
        os.utime(staged, ns=(7_000_000_000, 7_000_000_000))
        build_bundle_staging([str(src)], str(out))

        assert staged.read_bytes() == b"x" * 99 + b"y"

    def test_identical_content_is_still_not_rewritten(self, tmp_path, monkeypatch):
        src = tmp_path / "src"
        _put(src / "assets/m/data.bin", b"A" * 5000)
        out = tmp_path / "out"
        build_bundle_staging([str(src)], str(out))
        monkeypatch.setattr(
            bundle_service, "_copy_file", lambda s, d: pytest.fail("內容相同不應重寫")
        )

        stats = build_bundle_staging([str(src)], str(out))

        assert stats["written"] == 0 and stats["unchanged"] == 1

    def test_comparison_does_not_use_a_stale_result_cache(self, tmp_path):
        """同一行程內連續比對：內容在大小與 mtime 不變下改變，第二次必須看見。"""
        a = _put(tmp_path / "a.bin", b"AAAA")
        b = _put(tmp_path / "b.bin", b"AAAA")
        for p in (a, b):
            os.utime(p, ns=(9_000_000_000, 9_000_000_000))
        assert bundle_service._same_file_content(str(a), str(b)) is True

        b.write_bytes(b"BBBB")
        os.utime(b, ns=(9_000_000_000, 9_000_000_000))

        assert bundle_service._same_file_content(str(a), str(b)) is False


class TestCaseInsensitiveFileSystems:
    """review P2：Windows 上僅大小寫不同的路徑視為同一個檔案，staging 的實體拼法必須收斂到來源。

    測試在大小寫敏感的 Linux 上以 ``normcase = lower`` 模擬 Windows 的比對規則；
    ``os.rename`` 在 NTFS 上支援僅大小寫不同的改名。
    """

    @pytest.fixture(autouse=True)
    def _windows_like_normcase(self, monkeypatch):
        monkeypatch.setattr(os.path, "normcase", str.lower)

    @staticmethod
    def _tree(root: Path) -> list[str]:
        return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))

    def test_directory_case_rename_converges_without_content_change(self, tmp_path):
        src = tmp_path / "src"
        _lang(src, "modid", {"a": "1"})
        out = tmp_path / "out"
        build_bundle_staging([str(src)], str(out))

        (src / "assets" / "modid").rename(src / "assets" / "ModID")  # 內容完全沒變
        stats = build_bundle_staging([str(src)], str(out))

        assert self._tree(out) == [
            "assets",
            "assets/ModID",
            "assets/ModID/lang",
            "assets/ModID/lang/zh_tw.json",
        ], "ZIP 項目名稱來自 staging 的實體路徑，拼法必須與來源一致"
        assert stats["removed"] == 0 and stats["written"] == 0
        assert json.loads(
            (out / "assets/ModID/lang/zh_tw.json").read_text("utf-8")
        ) == {"a": "1"}

    def test_file_name_case_rename_converges(self, tmp_path):
        src = tmp_path / "src"
        f = _lang(src, "m", {"a": "1"})
        out = tmp_path / "out"
        build_bundle_staging([str(src)], str(out))

        f.rename(f.with_name("ZH_TW.json"))
        build_bundle_staging([str(src)], str(out))

        assert self._tree(out) == [
            "assets",
            "assets/m",
            "assets/m/lang",
            "assets/m/lang/ZH_TW.json",
        ]

    def test_nested_and_unrelated_entries_are_handled(self, tmp_path):
        src = tmp_path / "src"
        _put(src / "assets/mod/lang/a.json", '{"a": 1}')
        _put(src / "assets/other/lang/keep.json", '{"k": 1}')
        out = tmp_path / "out"
        build_bundle_staging([str(src)], str(out))

        (src / "assets" / "mod").rename(src / "assets" / "MOD")
        (src / "assets" / "MOD" / "lang").rename(src / "assets" / "MOD" / "LANG")
        build_bundle_staging([str(src)], str(out))

        assert self._tree(out) == [
            "assets",
            "assets/MOD",
            "assets/MOD/LANG",
            "assets/MOD/LANG/a.json",
            "assets/other",
            "assets/other/lang",
            "assets/other/lang/keep.json",
        ]

    def test_first_source_decides_the_spelling_when_sources_differ_in_case(
        self, tmp_path
    ):
        low, high = tmp_path / "low", tmp_path / "high"
        _lang(low, "ModID", {"k1": "low", "k2": "low"})
        _lang(high, "modid", {"k2": "high"})  # 另一個來源用不同大小寫
        out = tmp_path / "out"

        stats = build_bundle_staging([str(low), str(high)], str(out))

        assert self._tree(out) == [
            "assets",
            "assets/ModID",
            "assets/ModID/lang",
            "assets/ModID/lang/zh_tw.json",
        ], "與舊版完整重建一致：先寫入的來源決定拼法，後面的合併進同一個檔案"
        assert json.loads(
            (out / "assets/ModID/lang/zh_tw.json").read_text("utf-8")
        ) == {
            "k1": "low",
            "k2": "high",
        }
        assert stats["merged"] == 1

    def test_second_run_after_case_convergence_is_a_noop(self, tmp_path, monkeypatch):
        src = tmp_path / "src"
        _lang(src, "modid", {"a": "1"})
        out = tmp_path / "out"
        build_bundle_staging([str(src)], str(out))
        (src / "assets" / "modid").rename(src / "assets" / "ModID")
        build_bundle_staging([str(src)], str(out))

        stats = build_bundle_staging([str(src)], str(out))

        assert stats["written"] == 0 and stats["removed"] == 0


class TestLargeTree:
    """#158 驗收：大型目錄、重複執行、來源變更。"""

    @pytest.fixture
    def big_sources(self, tmp_path):
        low, mid, high = tmp_path / "low", tmp_path / "mid", tmp_path / "high"
        for i in range(40):
            for j in range(10):  # 每個 mod 10 個單一來源檔
                _put(low / f"assets/mod{i}/textures/t{j}.png", f"png-{i}-{j}".encode())
            _lang(
                low, f"mod{i}", {"a": f"low{i}", "b": "low"}
            )  # 與 mid／high 重疊的 JSON
            _lang(mid, f"mod{i}", {"b": f"mid{i}"})
            if i % 2:
                _lang(high, f"mod{i}", {"c": f"high{i}"})
        return [str(low), str(mid), str(high)]

    def test_second_pass_writes_nothing_and_matches_clean_rebuild(
        self, tmp_path, big_sources
    ):
        out, ref = tmp_path / "out", tmp_path / "ref"
        first = build_bundle_staging(big_sources, str(out))
        second = build_bundle_staging(big_sources, str(out))
        _reference_full_rebuild(big_sources, str(ref))

        outputs = 40 * 10 + 40  # 單一來源檔 + 合併的語言檔
        assert first["written"] == outputs
        assert second["written"] == 0 and second["unchanged"] == outputs
        assert second["removed"] == 0
        assert _snapshot(out) == _snapshot(ref)

    def test_changes_in_a_big_tree_only_touch_the_affected_outputs(
        self, tmp_path, big_sources, monkeypatch
    ):
        out, ref = tmp_path / "out", tmp_path / "ref"
        build_bundle_staging(big_sources, str(out))
        low, mid = Path(big_sources[0]), Path(big_sources[1])
        (low / "assets/mod3/textures/t1.png").write_bytes(b"changed")  # 改一個
        (low / "assets/mod7/textures/t2.png").unlink()  # 刪一個
        _lang(mid, "mod5", {"b": "mid5-new"})  # 合併語言檔的來源之一
        _put(low / "assets/mod40/textures/new.png", b"new")  # 增一個

        stats = build_bundle_staging(big_sources, str(out))

        assert stats["written"] == 3  # 改 + 合併 + 新增
        assert stats["removed"] == 1
        _reference_full_rebuild(big_sources, str(ref))
        assert _snapshot(out) == _snapshot(ref)
