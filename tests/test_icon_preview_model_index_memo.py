"""Task 9 / B5：model index 每個 JAR 只讀一次磁碟快取；載入在執行緒執行。"""

import asyncio
import os
import zipfile

from app.views import icon_preview_view as ipv


def test_model_index_read_from_disk_once_and_invalidated_on_jar_change(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(ipv, "_get_model_index_cache_dir", lambda: tmp_path / "idx")
    monkeypatch.setattr(ipv, "_MODEL_INDEX_MEMO", {})
    jar = tmp_path / "demo-1.0.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("assets/demo/models/item/a.json", "{}")

    ipv._save_model_index_to_cache(jar, "demo", {"item/a": ["x"]})
    ipv._MODEL_INDEX_MEMO.clear()

    reads = []
    orig = ipv._load_model_index_from_disk
    monkeypatch.setattr(
        ipv, "_load_model_index_from_disk", lambda *a: reads.append(1) or orig(*a)
    )

    for _ in range(50):
        assert ipv._load_model_index_from_cache(jar, "demo") == {"item/a": ["x"]}
    assert len(reads) == 1

    # JAR 更新（mtime/size 改變）→ 快取失效
    with zipfile.ZipFile(jar, "a") as zf:
        zf.writestr("assets/demo/models/item/b.json", "{}")
    st = jar.stat()
    os.utime(jar, (st.st_atime, st.st_mtime + 10))
    assert ipv._load_model_index_from_cache(jar, "demo") is None


def test_load_runs_scan_off_event_loop(tmp_path, monkeypatch):
    from tests.conftest import mock_page

    page = mock_page()
    view = ipv.IconPreviewView(page)
    view.update = lambda *a: None
    view.source_root = tmp_path
    view.review_root = tmp_path
    monkeypatch.setattr(view, "_detect_source_mode", lambda: "extracted_folder")
    scanned = []
    monkeypatch.setattr(
        view, "_scan_entries", lambda mode, total: scanned.append(mode) or []
    )

    view._on_load_clicked(None)
    assert scanned == []  # 點擊當下不在 event loop 上掃描
    assert view._loading is True and view.load_btn.disabled is True

    view._on_load_clicked(None)  # 掃描中重複點擊會被忽略
    assert len(page._tasks) == 1

    handler, args = page._tasks[0]
    asyncio.run(handler(*args))
    assert scanned == ["extracted_folder"]
    assert view._loading is False
