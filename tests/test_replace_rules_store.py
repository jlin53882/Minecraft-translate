"""替換規則 Store 的快取、版本與執行緒安全回歸測試。"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from translation_tool.utils import text_processor
from translation_tool.utils.replace_rules import ReplaceRules
from translation_tool.utils.text_processor import ReplaceRulesStore


def _store(tmp_path, load_count, *, delay=0.0):
    """建立使用測試路徑與載入器的替換規則 Store。"""
    rules_path = tmp_path / "rules.json"

    def loader(path):
        """載入測試資料並回傳規則內容，供快取與並行行為驗證。"""
        load_count.append(path)
        if delay:
            time.sleep(delay)
        rows = json.loads(path.read_text(encoding="utf-8"))
        return ReplaceRules(rows), len(rows), 0

    return ReplaceRulesStore(
        path_resolver=lambda path: rules_path if path == "rules.json" else path,
        loader=loader,
    ), rules_path


def test_store_reuses_immutable_snapshot_until_file_revision_changes(tmp_path):
    """驗證此測試案例的預期結果與相容性。"""
    loads = []
    store, rules_path = _store(tmp_path, loads)
    rules_path.write_text(
        json.dumps([{"from": "hello", "to": "哈囉"}], ensure_ascii=False),
        encoding="utf-8",
    )

    first = store.get_snapshot("rules.json")
    second = store.get_snapshot("rules.json")

    assert first is second
    assert len(loads) == 1
    assert first[0]["to"] == "哈囉"
    try:
        first[0]["to"] = "不可修改"
    except TypeError:
        pass
    else:  # pragma: no cover - 明確保護共享快照不可變
        raise AssertionError("共享規則快照不應允許修改")

    rules_path.write_text(
        json.dumps([{"from": "hello", "to": "你好"}], ensure_ascii=False),
        encoding="utf-8",
    )
    changed = store.get_snapshot("rules.json")
    assert len(loads) == 2
    assert changed is not first
    assert changed[0]["to"] == "你好"


def test_store_logs_only_actual_load_with_revision_and_source(tmp_path, monkeypatch):
    """驗證此測試案例的預期結果與相容性。"""
    loads = []
    store, rules_path = _store(tmp_path, loads)
    rules_path.write_text("[]", encoding="utf-8")
    messages = []
    monkeypatch.setattr(
        text_processor,
        "log_info",
        lambda message, *args, **_kwargs: messages.append((message, args)),
    )

    store.get_count("rules.json", source="dashboard")
    store.get_count("rules.json", source="dashboard")

    assert len(messages) == 1
    rendered = messages[0][0] % messages[0][1]
    assert "source=dashboard" in rendered
    assert "cache_hit=false" in rendered
    assert "path=" in rendered and "revision=mtime_ns=" in rendered
    assert "實際讀取耗時=" in rendered


def test_store_serializes_concurrent_first_loads(tmp_path):
    """驗證此測試案例的預期結果與相容性。"""
    loads = []
    active = 0
    max_active = 0
    state_lock = threading.Lock()
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(
        json.dumps([{"from": "hello", "to": "哈囉"}], ensure_ascii=False),
        encoding="utf-8",
    )

    def loader(path):
        """載入測試資料並回傳規則內容，供快取與並行行為驗證。"""
        nonlocal active, max_active
        with state_lock:
            loads.append(path)
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.03)
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
            return ReplaceRules(rows), len(rows), 0
        finally:
            with state_lock:
                active -= 1

    store = ReplaceRulesStore(
        path_resolver=lambda _path: rules_path,
        loader=loader,
    )
    with ThreadPoolExecutor(max_workers=8) as executor:
        snapshots = list(
            executor.map(lambda _index: store.get_snapshot("rules.json"), range(8))
        )

    assert len(loads) == 1
    assert max_active == 1
    assert all(snapshot is snapshots[0] for snapshot in snapshots)


def test_store_force_reload_and_invalidate_notify_and_reload(tmp_path):
    """驗證此測試案例的預期結果與相容性。"""
    loads = []
    store, rules_path = _store(tmp_path, loads)
    rules_path.write_text("[]", encoding="utf-8")
    events = []
    store.subscribe(lambda path: events.append(path))

    store.get_snapshot("rules.json")
    store.force_reload("rules.json", source="rules_manual_reload")
    store.invalidate("rules.json")
    store.get_snapshot("rules.json")

    assert len(loads) == 3
    assert events == [rules_path.resolve(), rules_path.resolve()]


def test_save_replace_rules_never_exposes_partial_json(tmp_path, monkeypatch):
    """驗證儲存期間的並行讀取只能取得完整規則快照。"""
    loads = []
    store, rules_path = _store(tmp_path, loads)
    rules_path.write_text(
        json.dumps([{"from": "hello", "to": "舊內容"}], ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(text_processor, "_resolve_rules_path", lambda _path: rules_path)
    monkeypatch.setattr(text_processor, "replace_rules_store", store)
    store.get_snapshot("rules.json")

    original_open = Path.open
    first_write = threading.Event()
    release_write = threading.Event()
    observed: dict[str, ReplaceRules] = {}

    def delayed_open(path, mode="r", *args, **kwargs):
        """讓測試在暫存檔半寫入時執行另一個快取讀取。"""
        handle = original_open(path, mode, *args, **kwargs)
        if path.parent != rules_path.parent or path == rules_path or "wb" not in mode:
            return handle

        class PartialWriter:
            """包裝暫存檔寫入以控制並行測試時序。"""

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                handle.close()

            def write(self, data):
                """先寫入部分資料，再等待讀取執行緒完成觀察。"""
                split = max(1, len(data) // 2)
                written = handle.write(data[:split])
                handle.flush()
                first_write.set()
                assert release_write.wait(5)
                return written + handle.write(data[split:])

            def flush(self):
                """轉送暫存檔 flush 操作。"""
                return handle.flush()

            def fileno(self):
                """轉送暫存檔 fileno 操作。"""
                return handle.fileno()

        return PartialWriter()

    monkeypatch.setattr(Path, "open", delayed_open)
    errors: list[BaseException] = []

    def save_rules():
        """在背景執行規則儲存。"""
        try:
            text_processor.save_replace_rules(
                "rules.json", [{"from": "hello", "to": "新內容"}], raise_on_error=True
            )
        except (AssertionError, OSError, RuntimeError) as exc:
            errors.append(exc)

    writer = threading.Thread(target=save_rules)
    writer.start()
    assert first_write.wait(5)

    def read_rules():
        """在寫入期間取得替換規則快照。"""
        observed["snapshot"] = store.get_snapshot("rules.json")

    reader = threading.Thread(target=read_rules)
    reader.start()
    release_write.set()
    writer.join(5)
    reader.join(5)

    assert not errors
    assert not writer.is_alive()
    assert not reader.is_alive()
    assert observed["snapshot"]
    assert observed["snapshot"][0]["to"] == "新內容"
