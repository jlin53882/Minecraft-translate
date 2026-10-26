"""逐 JAR 圖示掃描（``app.icon_jar_scan``）、執行器（``app.icon_scan_pool``）與 catalog 名稱讀取。"""

import json
import os
import struct
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app import icon_scan_pool
from app.icon_catalog import ModpackAssetCatalog, _central_directory_names
from app.icon_jar_scan import scan_jar
from app.icon_resolve import hit_to_uri, resolve_icon
from translation_tool.utils.cancellation import TaskCancelled, cancel_scope

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def make_jar(path: Path, files: dict[str, bytes | str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return path


def lang(**entries: str) -> str:
    return json.dumps(entries)


def pack(tmp_path: Path) -> Path:
    """兩個 JAR 的小型 modpack：物品／方塊（blockstate → cube_all）／實體備援／效果／無圖示。"""
    mods = tmp_path / "mods"
    make_jar(
        mods / "alpha-1.0.jar",
        {
            "assets/alpha/lang/en_us.json": lang(
                **{
                    "item.alpha.sword": "Sword",
                    "block.alpha.stone": "Stone",
                    "entity.alpha.worm": "Worm",
                    "item.alpha.plain": "Plain",
                    "jei.alpha.skip": "Skip",
                    "_comment": "no dot",
                }
            ),
            "assets/alpha/models/item/sword.json": json.dumps(
                {"parent": "item/generated", "textures": {"layer0": "alpha:item/sword"}}
            ),
            "assets/alpha/textures/item/sword.png": PNG,
            "assets/alpha/blockstates/stone.json": json.dumps(
                {"variants": {"": {"model": "alpha:block/stone"}}}
            ),
            "assets/alpha/models/block/stone.json": json.dumps(
                {"parent": "block/cube_all", "textures": {"all": "alpha:block/stone"}}
            ),
            "assets/alpha/textures/block/stone.png": PNG,
            "assets/alpha/models/item/worm.json": json.dumps(
                {"textures": {"layer0": "alpha:item/worm"}}
            ),
            "assets/alpha/textures/item/worm.png": PNG,
            "assets/alpha/textures/item/plain.png": PNG,
        },
    )
    make_jar(
        mods / "beta-2.0.jar",
        {
            "assets/beta/lang/en_us.json": lang(**{"effect.beta.speed": "Speed"}),
            "assets/beta/textures/mob_effect/speed.png": PNG,
        },
    )
    return mods


def icons(scan) -> dict[str, str | None]:
    return {key: icon for rows in scan.mods.values() for key, _en, icon in rows}


def test_scan_jar_reads_lang_and_resolves_icons(tmp_path):
    mods = pack(tmp_path)
    catalog = ModpackAssetCatalog.from_mods_directory(mods)
    try:
        scan = scan_jar(mods / "alpha-1.0.jar", catalog)
    finally:
        catalog.close()

    found = icons(scan)
    assert set(found) == {
        "item.alpha.sword",
        "block.alpha.stone",
        "entity.alpha.worm",
        "item.alpha.plain",
        "jei.alpha.skip",
        "_comment",
    }
    assert "textures/item/sword.png" in found["item.alpha.sword"]
    assert "textures/block/stone.png" in found["block.alpha.stone"]
    assert "textures/item/worm.png" in found["entity.alpha.worm"]
    assert "textures/item/plain.png" in found["item.alpha.plain"]
    assert found["jei.alpha.skip"] is None  # 非遊戲內容 key：不解析
    assert found["_comment"] is None
    assert scan.jar_name == "alpha-1.0.jar"
    assert not scan.failed


def test_scan_jar_icons_equal_direct_per_key_resolution(tmp_path):
    """冷載入的 icon 與逐 key 呼叫 ``resolve_icon`` 的結果完全一致。"""
    mods = pack(tmp_path)
    catalog = ModpackAssetCatalog.from_mods_directory(mods)
    try:
        for jar in sorted(mods.glob("*.jar")):
            scan = scan_jar(jar, catalog)
            with zipfile.ZipFile(jar) as zf:
                for modid, rows in scan.mods.items():
                    for key, _en, icon in rows:
                        hit = resolve_icon(
                            jar, modid, key, catalog, current_zf=zf, budget=None
                        )
                        assert icon == (hit_to_uri(hit) if hit else None), key
    finally:
        catalog.close()


def test_scan_jar_opens_the_jar_once(tmp_path):
    mods = pack(tmp_path)
    catalog = ModpackAssetCatalog.from_mods_directory(mods)
    opened = []
    real = zipfile.ZipFile

    def counting(*args, **kwargs):
        opened.append(args[0])
        return real(*args, **kwargs)

    try:
        with patch("zipfile.ZipFile", side_effect=counting):
            scan_jar(mods / "alpha-1.0.jar", catalog)
    finally:
        catalog.close()

    assert [Path(p).name for p in opened] == ["alpha-1.0.jar"]


def test_scan_jar_without_catalog_reads_lang_only(tmp_path):
    mods = pack(tmp_path)
    scan = scan_jar(mods / "alpha-1.0.jar", None)
    assert len(icons(scan)) == 6
    assert all(icon is None for icon in icons(scan).values())


def test_scan_jar_handles_bad_missing_and_bom(tmp_path):
    mods = tmp_path / "mods"
    mods.mkdir()
    (mods / "bad.jar").write_bytes(b"not a zip")
    make_jar(
        mods / "bom.jar",
        {"assets/bom/lang/en_us.json": "﻿" + lang(**{"item.bom.x": "X"})},
    )
    assert scan_jar(mods / "bad.jar", None).failed
    assert scan_jar(mods / "missing.jar", None).failed
    assert icons(scan_jar(mods / "bom.jar", None)) == {"item.bom.x": None}


def test_scan_jar_budget_exceeded_keeps_rows_without_icons(tmp_path, monkeypatch):
    """圖示解析累計讀取超限：保留 lang 項目，之後的 key 不再解析。"""
    from translation_tool.utils.zip_safety import ArchiveBudgetError

    mods = pack(tmp_path)
    calls = []

    def exhausted(*args, **kwargs):
        calls.append(1)
        raise ArchiveBudgetError("budget")

    monkeypatch.setattr("app.icon_jar_scan.resolve_icon", exhausted)
    catalog = ModpackAssetCatalog.from_mods_directory(mods)
    try:
        scan = scan_jar(mods / "alpha-1.0.jar", catalog)
    finally:
        catalog.close()

    assert len(icons(scan)) == 6
    assert all(icon is None for icon in icons(scan).values())
    assert len(calls) == 1  # 超限後不再嘗試


# ---------------------------------------------------------------------------
# 執行器
# ---------------------------------------------------------------------------


def many_jars(tmp_path: Path, count: int) -> tuple[Path, list[Path]]:
    mods = tmp_path / "mods"
    jars = []
    for number in range(count):
        modid = f"mod{number:03d}"
        jars.append(
            make_jar(
                mods / f"{modid}.jar",
                {
                    f"assets/{modid}/lang/en_us.json": lang(
                        **{f"item.{modid}.thing": "Thing"}
                    ),
                    f"assets/{modid}/textures/item/thing.png": PNG,
                },
            )
        )
    return mods, jars


def summarize(scans) -> dict[str, dict]:
    return {scan.jar_name: icons(scan) for scan in scans}


def test_stream_threads_and_processes_give_identical_results(tmp_path, monkeypatch):
    if (os.cpu_count() or 1) < 2:
        pytest.skip("需要至少 2 核才會啟動行程池")
    mods, jars = many_jars(tmp_path, 6)
    expected = summarize(icon_scan_pool.stream_jar_scans(mods, jars, workers=1))
    monkeypatch.setattr(icon_scan_pool, "PROCESS_MIN_JARS", 2)
    used = []
    real_start = icon_scan_pool._start_process_pool

    def spy(*args, **kwargs):
        used.append("process")
        return real_start(*args, **kwargs)

    monkeypatch.setattr(icon_scan_pool, "_start_process_pool", spy)

    got = summarize(icon_scan_pool.stream_jar_scans(mods, jars, workers=2))

    assert used == ["process"]
    assert got == expected
    assert all(
        "textures/item/thing.png" in icon
        for icons_ in got.values()
        for icon in icons_.values()
    )


def test_stream_falls_back_to_threads_when_pool_cannot_start(tmp_path, monkeypatch):
    mods, jars = many_jars(tmp_path, 4)
    monkeypatch.setattr(icon_scan_pool, "PROCESS_MIN_JARS", 2)

    def boom(*_args, **_kwargs):
        raise OSError("no processes")

    monkeypatch.setattr(icon_scan_pool, "_start_process_pool", boom)

    got = summarize(icon_scan_pool.stream_jar_scans(mods, jars, workers=2))

    assert len(got) == 4
    assert all(any(icon_row.values()) for icon_row in got.values())


def test_stream_cancellation_raises_task_cancelled(tmp_path):
    mods, jars = many_jars(tmp_path, 4)
    with cancel_scope(lambda: True), pytest.raises(TaskCancelled):
        list(icon_scan_pool.stream_jar_scans(mods, jars, workers=1))


def test_stream_marks_crashing_worker_job_as_failed_and_continues(
    tmp_path, monkeypatch
):
    mods, jars = many_jars(tmp_path, 3)
    real = icon_scan_pool.scan_jar

    def flaky(path, catalog):
        if path.name == "mod001.jar":
            raise RuntimeError("boom")
        return real(path, catalog)

    monkeypatch.setattr(icon_scan_pool, "scan_jar", flaky)

    scans = {
        s.jar_name: s for s in icon_scan_pool.stream_jar_scans(mods, jars, workers=1)
    }

    assert scans["mod001.jar"].failed
    assert not scans["mod000.jar"].failed
    assert not scans["mod002.jar"].failed


# ---------------------------------------------------------------------------
# catalog 名稱讀取
# ---------------------------------------------------------------------------


def test_central_directory_names_match_zipfile(tmp_path):
    jar = make_jar(
        tmp_path / "a.jar",
        {
            "assets/a/lang/en_us.json": "{}",
            "assets/a/textures/x.png": PNG,
            "中文.txt": "x",
        },
    )
    with zipfile.ZipFile(jar, "a") as zf:
        zf.comment = b"archive comment"
    assert _central_directory_names(jar) == zipfile.ZipFile(jar).namelist()


def test_central_directory_names_defers_odd_archives_to_zipfile(tmp_path):
    prefixed = tmp_path / "prefixed.jar"
    make_jar(prefixed, {"assets/a/models/m.json": "{}"})
    prefixed.write_bytes(b"junk-before-zip" + prefixed.read_bytes())
    broken = tmp_path / "broken.jar"
    # 宣稱有 3 個成員、中央目錄 10 位元組，但檔案裡根本沒有
    broken.write_bytes(b"PK\x05\x06" + struct.pack("<HHHHIIH", 0, 0, 3, 3, 10, 0, 0))

    assert _central_directory_names(prefixed) is None  # 附加資料：交給 zipfile
    assert _central_directory_names(broken) is None

    catalog = ModpackAssetCatalog([prefixed, broken])
    try:
        assert catalog.has("assets/a/models/m.json")  # 退回 zipfile 後仍可索引
    finally:
        catalog.close()
