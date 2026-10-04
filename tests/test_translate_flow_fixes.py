"""tests/test_translate_flow_fixes.py

翻譯流程穩定性（#65 C-1、C-2、C-3）與快取分片寫入：
- C-2：API 連線階段暫時性錯誤會重試；HTTP 狀態 / 讀取逾時 / TLS 不重試
- C-3：批次縮到極限回填的原文要標記，且不可寫入快取
- C-1：共用迴圈以「實際回傳筆數」切片，不重翻也不漏翻
- 快取分片：force_new_shard / 超量寫入產生時間戳分片
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import Mock, patch

import orjson as json
import pytest
import requests

from translation_tool.core import lm_api_client
from translation_tool.core import lm_translator_shared_loop as loop_mod
from translation_tool.utils import cache_shards

# ---------------------------------------------------------------------------
# C-2：API retry
# ---------------------------------------------------------------------------

_OK_BODY = {"candidates": [{"content": {"parts": [{"text": "OK"}]}}]}


def _ok_response():
    resp = Mock()
    resp.ok = True
    resp.json.return_value = _OK_BODY
    return resp


def _call():
    return lm_api_client.call_gemini_requests(
        model_name="m",
        system_prompt="s",
        payload={"items": [{"id": "0", "value": "source"}]},
        api_key="k",
        temperature=0.1,
    )


@pytest.fixture
def api_env():
    with (
        patch.object(lm_api_client, "load_config", return_value={}),
        patch.object(lm_api_client, "interruptible_sleep") as sleep,
        patch.object(lm_api_client.requests, "post") as post,
    ):
        yield post, sleep


def test_retries_connection_error_then_succeeds(api_env):
    post, sleep = api_env
    post.side_effect = [
        requests.exceptions.ConnectionError("boom"),
        requests.exceptions.ConnectTimeout("slow"),
        _ok_response(),
    ]

    assert _call() == "OK"
    assert post.call_count == 3
    assert sleep.call_count == 2


def test_gives_up_after_max_attempts_and_reraises(api_env):
    post, sleep = api_env
    post.side_effect = requests.exceptions.ConnectionError("down")

    with pytest.raises(requests.exceptions.ConnectionError):
        _call()

    assert post.call_count == lm_api_client.NETWORK_RETRY_ATTEMPTS
    assert sleep.call_count == lm_api_client.NETWORK_RETRY_ATTEMPTS - 1


def test_backoff_grows_between_attempts(api_env):
    post, sleep = api_env
    post.side_effect = requests.exceptions.ConnectionError("down")

    with pytest.raises(requests.exceptions.ConnectionError):
        _call()

    waits = [c.args[0] for c in sleep.call_args_list]
    base = lm_api_client.NETWORK_RETRY_BASE_SEC
    assert base <= waits[0] <= 2 * base
    assert 2 * base <= waits[1] <= 3 * base


@pytest.mark.parametrize(
    "exc",
    [
        requests.exceptions.ReadTimeout("read"),
        requests.exceptions.SSLError("tls"),
    ],
)
def test_non_connection_errors_are_not_retried(api_env, exc):
    post, sleep = api_env
    post.side_effect = exc

    with pytest.raises(type(exc)):
        _call()

    assert post.call_count == 1
    sleep.assert_not_called()


def test_http_error_status_is_not_retried(api_env):
    """429/503 等狀態由 lm_translator_main 處理（換 key / 等待），這層不重試。"""
    post, sleep = api_env
    resp = Mock()
    resp.ok = False
    resp.status_code = 429
    resp.text = "quota"
    post.return_value = resp

    with pytest.raises(requests.HTTPError):
        _call()

    assert post.call_count == 1
    sleep.assert_not_called()


# ---------------------------------------------------------------------------
# C-3：回填原文標記
# ---------------------------------------------------------------------------


@patch("translation_tool.core.lm_translator_main.interruptible_sleep")
@patch("translation_tool.core.lm_translator_main.call_gemini_requests")
@patch("translation_tool.core.lm_config_rules._get_all_keys")
@patch("translation_tool.core.lm_translator_main.load_config")
def test_fallback_items_are_marked_untranslated(
    mock_config, mock_key, mock_call, _mock_sleep
):
    from translation_tool.core.lm_translator_main import translate_batch_smart

    mock_config.return_value = {
        "lm_translator": {
            "initial_batch_size_lang": 300,
            "batch_shrink_factor": 0.75,
            "min_batch_size": 50,
            "models": {"gemini-pro": {"enabled": True}},
        }
    }
    mock_key.return_value = ["k"]
    mock_call.return_value = '{"items": ['  # 永遠被截斷，batch 縮到極限

    items = [
        {"path": f"k{i}", "text": f"text{i}", "cache_type": "lang"} for i in range(5)
    ]
    result, _status = translate_batch_smart(items, len(items))

    assert [r["text"] for r in result] == [f"text{i}" for i in range(5)]
    assert all(r.get("_untranslated") is True for r in result)
    # 原始輸入不可被修改
    assert all("_untranslated" not in it for it in items)


def _patch_loop(monkeypatch):
    added: list[tuple] = []
    monkeypatch.setattr(loop_mod, "reload_translation_cache", lambda: None)
    monkeypatch.setattr(loop_mod, "add_to_cache", lambda *a, **k: added.append(a))
    monkeypatch.setattr(loop_mod, "save_translation_cache", lambda *a, **k: None)
    return added


def _items(n):
    return [
        {
            "path": f"k{i}",
            "text": f"t{i}",
            "source_text": f"t{i}",
            "cache_type": "kubejs",
        }
        for i in range(n)
    ]


def test_shared_loop_does_not_cache_untranslated_fallback(monkeypatch):
    added = _patch_loop(monkeypatch)
    delivered: list[dict] = []

    def fake_translate(batch, _total):
        out = []
        for it in batch:
            if it["path"] == "k1":
                out.append({**it, "_untranslated": True})  # 原文回填
            else:
                out.append({**it, "text": "譯"})
        return out, "AUTO"

    res = loop_mod.translate_items_with_cache_loop(
        _items(3),
        translate_batch_smart=fake_translate,
        batch_size_by_type={"kubejs": 10},
        sleep_seconds_between_batches=0,
        on_translated_item=delivered.append,
    )

    assert res.status == "DONE"
    assert res.processed == 3
    # 輸出仍含三筆（結構完整），但只有真正翻譯的兩筆進快取
    assert len(delivered) == 3
    assert sorted(a[1].split("|")[0] for a in added) == ["k0", "k2"]


# ---------------------------------------------------------------------------
# C-1：以實際回傳筆數切片
# ---------------------------------------------------------------------------


def test_shared_loop_partial_prefix_retries_only_unreturned(monkeypatch):
    """API 只回前 3 筆（PARTIAL）時，剩下 7 筆下一輪重送，不丟失也不重複。"""
    _patch_loop(monkeypatch)
    seen_batches: list[list[str]] = []

    def fake_translate(batch, _total):
        seen_batches.append([it["path"] for it in batch])
        if len(seen_batches) == 1:
            return [{**it, "text": "譯"} for it in batch[:3]], "PARTIAL"
        return [{**it, "text": "譯"} for it in batch], "AUTO"

    res = loop_mod.translate_items_with_cache_loop(
        _items(10),
        translate_batch_smart=fake_translate,
        batch_size_by_type={"kubejs": 10},
        sleep_seconds_between_batches=0,
    )

    assert res.status == "DONE"
    assert res.processed == 10
    assert seen_batches[0] == [f"k{i}" for i in range(10)]
    assert seen_batches[1] == [f"k{i}" for i in range(3, 10)]


def _run_with_malformed(monkeypatch, n, bad_index, bad_value=None):
    added = _patch_loop(monkeypatch)
    calls: list[list[str]] = []
    delivered: list[dict] = []

    def fake_translate(batch, _total):
        calls.append([it["path"] for it in batch])
        out = [{**it, "text": "譯"} for it in batch]
        out[bad_index] = {"path": f"k{bad_index}"} if bad_value is None else bad_value
        return out, "AUTO"

    res = loop_mod.translate_items_with_cache_loop(
        _items(n),
        translate_batch_smart=fake_translate,
        batch_size_by_type={"kubejs": 10},
        sleep_seconds_between_batches=0,
        on_translated_item=delivered.append,
    )
    return res, added, calls, delivered


@pytest.mark.parametrize("bad_index", [0, 1, 3])
def test_shared_loop_malformed_item_falls_back_not_dropped(monkeypatch, bad_index):
    """格式無效的結果（首 / 中 / 尾）以原文回填：不重送、不進快取、輸出完整、processed == total。"""
    res, added, calls, delivered = _run_with_malformed(monkeypatch, 4, bad_index)

    assert len(calls) == 1  # 位置對得上，不會因筆數問題再送一次
    assert res.status == "DONE"
    assert res.processed == res.total == 4
    assert [d["path"] for d in delivered] == ["k0", "k1", "k2", "k3"]
    bad = delivered[bad_index]
    assert bad["_untranslated"] is True
    assert bad["text"] == f"t{bad_index}"  # 原文回填
    cached = sorted(a[1].split("|")[0] for a in added)
    assert cached == sorted(f"k{i}" for i in range(4) if i != bad_index)
    assert res.last_error  # 可觀察：有 item 回填原文


def test_shared_loop_non_dict_result_falls_back(monkeypatch):
    res, _added, _calls, delivered = _run_with_malformed(
        monkeypatch, 3, 1, bad_value="oops"
    )
    assert res.processed == res.total == 3
    assert delivered[1]["_untranslated"] is True


def test_shared_loop_unrecoverable_malformed_item_fails_not_done(monkeypatch):
    """原始項目本身缺欄位 → 無法回填，不可回 DONE。"""
    _patch_loop(monkeypatch)

    def fake_translate(batch, _total):
        return [{"path": "x"}], "AUTO"

    items = [{"path": "k0", "cache_type": "kubejs"}]  # 缺 text / source_text
    res = loop_mod.translate_items_with_cache_loop(
        items,
        translate_batch_smart=fake_translate,
        batch_size_by_type={"kubejs": 10},
        sleep_seconds_between_batches=0,
    )
    assert res.status == "FAILED"


def test_shared_loop_done_implies_all_processed(monkeypatch):
    """completion invariant：DONE 不可在 processed < total 時出現（含 PARTIAL + malformed 混合）。"""
    _patch_loop(monkeypatch)
    n = {"calls": 0}

    def fake_translate(batch, _total):
        n["calls"] += 1
        out = [{**it, "text": "譯"} for it in batch]
        if n["calls"] == 1:
            out = out[:3]
            out[0] = {"path": "bad"}
            return out, "PARTIAL"
        return out, "AUTO"

    res = loop_mod.translate_items_with_cache_loop(
        _items(10),
        translate_batch_smart=fake_translate,
        batch_size_by_type={"kubejs": 10},
        sleep_seconds_between_batches=0,
    )
    assert not (res.status == "DONE" and res.processed < res.total)
    assert res.status == "DONE"
    assert res.processed == 10


# ---------------------------------------------------------------------------
# 快取分片：時間戳分片
# ---------------------------------------------------------------------------


def _save(type_dir: Path, entries: dict, *, force_new_shard: bool, size: int = 100):
    cache_shards._save_entries_to_active_shards(
        type_dir=type_dir,
        cache_type="lang",
        entries=entries,
        rolling_shard_size=size,
        active_shard_file=".active",
        force_new_shard=force_new_shard,
    )


def test_timestamp_shard_gets_sequence_suffix_when_name_collides(tmp_path):
    type_dir = tmp_path / "lang"
    type_dir.mkdir()

    with patch.object(cache_shards, "datetime") as fake_dt:
        fake_dt.now.return_value.strftime.return_value = "0101010101"
        _save(type_dir, {"a": 1}, force_new_shard=True)
        _save(type_dir, {"b": 2}, force_new_shard=True)

    names = sorted(p.name for p in type_dir.glob("lang_??????????-*.json"))
    assert names == ["lang_0101010101-1.json", "lang_0101010101-2.json"]
    assert json.loads((type_dir / names[0]).read_bytes()) == {"a": 1}
    assert json.loads((type_dir / names[1]).read_bytes()) == {"b": 2}


def test_oversized_write_goes_to_timestamp_shard_whole(tmp_path):
    """一次寫入超過分片上限時，整批寫入一個時間戳分片，不拆成多個半滿分片。"""
    type_dir = tmp_path / "lang"
    type_dir.mkdir()
    entries = {f"k{i}": i for i in range(25)}

    _save(type_dir, entries, force_new_shard=False, size=10)

    files = list(type_dir.glob("lang_??????????-*.json"))
    assert len(files) == 1
    assert re.match(r"lang_\d{10}-1\.json", files[0].name)
    assert len(json.loads(files[0].read_bytes())) == 25
    assert not (type_dir / "lang_00002.json").exists()


def test_timestamp_shards_are_loadable_and_do_not_break_active_detection(tmp_path):
    """時間戳分片檔名含 '-'，不可被誤判為編號分片。"""
    type_dir = tmp_path / "lang"
    type_dir.mkdir()
    (type_dir / "lang_00003.json").write_bytes(json.dumps({"x": 1}))
    (type_dir / "lang_0630123456-1.json").write_bytes(json.dumps({"y": 2}))

    active = cache_shards._get_active_shard_path(
        type_dir=type_dir, cache_type="lang", active_shard_file=".active"
    )

    assert active.name == "lang_00003.json"


# ---------------------------------------------------------------------------
# batch_write_interval
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cfg_value", "expected"),
    [(None, 2), (3, 3), ("4", 4), (0, 1), (-5, 1), ("abc", 2)],
)
def test_batch_write_interval_normalization(cfg_value, expected):
    from translation_tool.utils.config_manager import get_batch_write_interval

    cfg = {"lm_translator": {}}
    if cfg_value is not None:
        cfg["lm_translator"]["batch_write_interval"] = cfg_value
    assert get_batch_write_interval(cfg) == expected
    assert get_batch_write_interval({}) == 2  # 缺 lm_translator 區段


@pytest.mark.parametrize(
    ("cfg_value", "expected"),
    [(None, 2), (0, 1), (-5, 1), ("4", 4), ("abc", 2)],
)
def test_runtime_and_ui_share_one_batch_write_interval_source(cfg_value, expected):
    """runtime 實際使用值與 UI 顯示值都走 config_manager.get_batch_write_interval。"""
    from translation_tool.core import lm_translator
    from translation_tool.utils import config_manager

    cfg = {"lm_translator": {}}
    if cfg_value is not None:
        cfg["lm_translator"]["batch_write_interval"] = cfg_value
    with patch.object(config_manager, "load_config", return_value=cfg):
        assert lm_translator._get_batch_write_interval() == expected
        assert config_manager.get_batch_write_interval() == expected


def test_lm_view_uses_shared_batch_write_interval_helper():
    src = (Path(__file__).parent.parent / "app/views/lm_view.py").read_text(
        encoding="utf-8"
    )
    assert "get_batch_write_interval()" in src
    assert '.get("batch_write_interval"' not in src


# ---------------------------------------------------------------------------
# 快取分片 freshness contract：載入順序 = 寫入先後，與檔名無關
# ---------------------------------------------------------------------------


def _load(tmp_path: Path) -> dict:
    import logging

    from translation_tool.utils.cache_loader import load_cache_type

    cache: dict = {}
    load_cache_type(
        "lang",
        translation_cache=cache,
        cache_file_path={},
        cache_root=tmp_path,
        parallel_workers=2,
        logger=logging.getLogger("t"),
    )
    return cache["lang"]


def test_freshness_numbered_old_then_timestamp_newer(tmp_path):
    """Case A：編號分片舊 → 時間戳分片新 → 重載，新值勝出。"""
    type_dir = tmp_path / "lang"
    type_dir.mkdir()
    _save(type_dir, {"K": "A"}, force_new_shard=False)
    _save(type_dir, {"K": "B"}, force_new_shard=True)
    assert _load(tmp_path)["K"] == "B"


def test_freshness_timestamp_old_then_numbered_newer(tmp_path):
    """Case B：時間戳分片舊 → 編號分片新 → 重載，新值勝出（檔名排序會讓舊值勝出）。"""
    type_dir = tmp_path / "lang"
    type_dir.mkdir()
    _save(type_dir, {"K": "B"}, force_new_shard=True)
    _save(type_dir, {"K": "C"}, force_new_shard=False)
    assert _load(tmp_path)["K"] == "C"


def test_freshness_three_writes_newest_wins(tmp_path):
    """Case C：numbered(A) → timestamp(B) → numbered 更新(C) → timestamp(D)，最後一次寫入勝出。"""
    type_dir = tmp_path / "lang"
    type_dir.mkdir()
    _save(type_dir, {"K": "A"}, force_new_shard=False)
    _save(type_dir, {"K": "B"}, force_new_shard=True)
    assert _load(tmp_path)["K"] == "B"
    _save(type_dir, {"K": "C"}, force_new_shard=False)
    assert _load(tmp_path)["K"] == "C"
    _save(type_dir, {"K": "D"}, force_new_shard=True)
    assert _load(tmp_path)["K"] == "D"


def test_freshness_legacy_untracked_shards_keep_name_order_and_lose_to_tracked(
    tmp_path,
):
    type_dir = tmp_path / "lang"
    type_dir.mkdir()
    (type_dir / "lang_00001.json").write_bytes(json.dumps({"K": "legacy1", "L": 1}))
    (type_dir / "lang_00002.json").write_bytes(json.dumps({"K": "legacy2"}))
    assert _load(tmp_path)["K"] == "legacy2"
    _save(type_dir, {"K": "new"}, force_new_shard=True)
    assert _load(tmp_path)["K"] == "new"


def test_shard_order_file_is_not_treated_as_a_shard(tmp_path):
    type_dir = tmp_path / "lang"
    type_dir.mkdir()
    _save(type_dir, {"K": 1}, force_new_shard=True)
    assert (type_dir / cache_shards.SHARD_ORDER_FILE).exists()
    assert all(p.name != cache_shards.SHARD_ORDER_FILE for p in type_dir.glob("*.json"))


def test_timestamp_shard_failure_leaves_no_zero_byte_file(tmp_path):
    type_dir = tmp_path / "lang"
    type_dir.mkdir()
    with (
        patch.object(cache_shards, "_write_json_atomic", side_effect=OSError("boom")),
        pytest.raises(OSError, match="boom"),
    ):
        _save(type_dir, {"K": 1}, force_new_shard=True)
    assert list(type_dir.glob("lang_*.json")) == []


# ---------------------------------------------------------------------------
# 編號分片交易：資料與 .shard_order 同一個鎖、失敗時 rollback
# ---------------------------------------------------------------------------


def test_numbered_metadata_failure_rolls_back_shard_data(tmp_path):
    """序號更新失敗 → 分片還原成寫入前內容並拋出，不留下「新資料 + 舊序號」。"""
    type_dir = tmp_path / "lang"
    type_dir.mkdir()
    _save(type_dir, {"K": "A"}, force_new_shard=False)  # numbered = A
    _save(type_dir, {"K": "B"}, force_new_shard=True)  # timestamp = B（較新）
    numbered = type_dir / "lang_00001.json"
    order_before = (type_dir / cache_shards.SHARD_ORDER_FILE).read_bytes()

    real_record = cache_shards._record_shard_write

    def flaky_record(td, name):
        if name == numbered.name:
            raise OSError("order write failed")
        return real_record(td, name)

    with (
        patch.object(cache_shards, "_record_shard_write", side_effect=flaky_record),
        pytest.raises(OSError, match="order write failed"),
    ):
        _save(type_dir, {"K": "C"}, force_new_shard=False)

    assert json.loads(numbered.read_bytes()) == {"K": "A"}  # 已還原
    assert (type_dir / cache_shards.SHARD_ORDER_FILE).read_bytes() == order_before
    assert _load(tmp_path)["K"] == "B"  # 磁碟狀態與序號一致，不出現舊值蓋新值
    assert not list(type_dir.glob("*.tmp"))


def test_numbered_metadata_failure_on_new_shard_removes_it(tmp_path):
    type_dir = tmp_path / "lang"
    type_dir.mkdir()
    with (
        patch.object(cache_shards, "_record_shard_write", side_effect=OSError("boom")),
        pytest.raises(OSError, match="boom"),
    ):
        _save(type_dir, {"K": "A"}, force_new_shard=False)
    assert not (type_dir / "lang_00001.json").exists()


def test_concurrent_numbered_writers_do_not_lose_updates(tmp_path):
    """多個 writer 同時寫同一個 active 編號分片：read-modify-write 不可 lost update。"""
    import threading

    type_dir = tmp_path / "lang"
    type_dir.mkdir()
    threads_n, per_thread = 8, 15
    start = threading.Barrier(threads_n)
    errors: list[BaseException] = []

    def worker(tid: int):
        try:
            start.wait()
            for i in range(per_thread):
                _save(
                    type_dir,
                    {f"t{tid}-{i}": f"{tid}/{i}"},
                    force_new_shard=False,
                    size=10_000,
                )
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(threads_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    loaded = _load(tmp_path)
    assert len(loaded) == threads_n * per_thread


def test_numbered_rotation_inside_transaction_does_not_deadlock(tmp_path):
    """交易內旋轉使用 *_locked，不會對同一把非重入鎖再次加鎖。"""
    import threading

    type_dir = tmp_path / "lang"
    type_dir.mkdir()

    def run():
        for n in range(3):  # 每次 8 筆、分片上限 10：會跨分片並在交易內旋轉
            chunk = {f"k{n}-{i}": i for i in range(8)}
            _save(type_dir, chunk, force_new_shard=False, size=10)

    t = threading.Thread(target=run)
    t.start()
    t.join(timeout=10)
    assert not t.is_alive(), "旋轉時發生 nested-lock 死鎖"
    assert len(_load(tmp_path)) == 24
    assert (type_dir / ".active").read_text().strip() == "00003"


def test_timestamp_shard_does_not_touch_active_pointer(tmp_path):
    type_dir = tmp_path / "lang"
    type_dir.mkdir()
    _save(type_dir, {"a": 1}, force_new_shard=False)
    before = (type_dir / ".active").read_text()
    _save(type_dir, {"b": 2}, force_new_shard=True)
    assert (type_dir / ".active").read_text() == before
