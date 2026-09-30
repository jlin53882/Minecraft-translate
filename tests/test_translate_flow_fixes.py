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
        payload={"items": []},
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
@patch("translation_tool.core.lm_translator_main.get_current_api_key")
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
    mock_key.return_value = "k"
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


def test_shared_loop_invalid_result_is_consumed_not_retried(monkeypatch):
    """回傳中有格式無效的項目：算已消耗、略過，不會讓尾端項目被重翻。"""
    added = _patch_loop(monkeypatch)
    calls: list[list[str]] = []

    def fake_translate(batch, _total):
        calls.append([it["path"] for it in batch])
        out = [{**it, "text": "譯"} for it in batch]
        out[1] = {"path": "k1"}  # 缺 text / source_text → 無效
        return out, "AUTO"

    res = loop_mod.translate_items_with_cache_loop(
        _items(4),
        translate_batch_smart=fake_translate,
        batch_size_by_type={"kubejs": 10},
        sleep_seconds_between_batches=0,
    )

    assert len(calls) == 1  # 不會因為筆數對不上而再送一次
    assert res.processed == 3
    assert sorted(a[1].split("|")[0] for a in added) == ["k0", "k2", "k3"]


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
def test_batch_write_interval_is_read_from_config(cfg_value, expected):
    from translation_tool.core import lm_translator

    cfg = {"lm_translator": {}}
    if cfg_value is not None:
        cfg["lm_translator"]["batch_write_interval"] = cfg_value
    with patch.object(lm_translator, "load_config", return_value=cfg):
        assert lm_translator._get_batch_write_interval() == expected


def test_load_cache_type_orders_shards_by_write_time(tmp_path):
    """時間戳分片先寫、編號分片後更新同一 key，重載後應取得較新的值。"""
    import logging
    import os

    from translation_tool.utils.cache_loader import load_cache_type

    type_dir = tmp_path / "lang"
    type_dir.mkdir()
    old = type_dir / "lang_0930120000-1.json"
    new = type_dir / "lang_00001.json"
    old.write_text('{"k": "old"}', encoding="utf-8")
    new.write_text('{"k": "new"}', encoding="utf-8")
    os.utime(old, ns=(1_000_000_000, 1_000_000_000))
    os.utime(new, ns=(2_000_000_000, 2_000_000_000))

    cache: dict = {}
    load_cache_type(
        "lang",
        translation_cache=cache,
        cache_file_path={},
        cache_root=tmp_path,
        parallel_workers=2,
        logger=logging.getLogger("t"),
    )
    assert cache["lang"]["k"] == "new"
