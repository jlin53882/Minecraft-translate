"""os.replace 暫時拒絕重試、`.active` 指向不存在分片時的快取卡片文字。"""

from __future__ import annotations

import os

import pytest

from app.views.cache_manager.cache_status_text import has_active_shard, shard_line
from translation_tool.utils import file_replace


def test_replace_retries_transient_permission_error(tmp_path, monkeypatch):
    src, dst = tmp_path / "a", tmp_path / "b"
    src.write_text("x")
    real, calls = os.replace, []

    def flaky(a, b):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError(5, "denied")
        real(a, b)

    monkeypatch.setattr(file_replace.os, "replace", flaky)
    monkeypatch.setattr(file_replace.time, "sleep", lambda _s: None)
    file_replace.replace_with_retry(src, dst)
    assert dst.read_text() == "x" and len(calls) == 3


def test_replace_raises_when_denial_persists(tmp_path, monkeypatch):
    def deny(a, b):
        raise PermissionError(5, "denied")

    monkeypatch.setattr(file_replace.os, "replace", deny)
    monkeypatch.setattr(file_replace.time, "sleep", lambda _s: None)
    with pytest.raises(PermissionError):
        file_replace.replace_with_retry(tmp_path / "a", tmp_path / "b")


def test_shard_line_without_numbered_active_shard():
    state = {
        "active_shard_id": "00001",
        "active_shard_exists": False,
        "shard_count": 103,
    }
    assert not has_active_shard(state)
    text = shard_line(state)
    assert "0 / 2,500" not in text and "103" in text
