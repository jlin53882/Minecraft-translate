"""translation_tool.utils.fs_utils.fsync_directory。"""

from __future__ import annotations

import os

import pytest

from translation_tool.utils import fs_utils


@pytest.mark.skipif(
    os.name == "nt" or not hasattr(os, "O_DIRECTORY"), reason="平台不支援目錄 fsync"
)
def test_fsync_directory_syncs_and_closes_the_descriptor(tmp_path, monkeypatch):
    synced: list[int] = []
    closed: list[int] = []
    real_fsync, real_close = os.fsync, os.close
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd)))
    monkeypatch.setattr(os, "close", lambda fd: (closed.append(fd), real_close(fd)))

    fs_utils.fsync_directory(tmp_path)

    assert len(synced) == 1 and closed == synced


@pytest.mark.skipif(
    os.name == "nt" or not hasattr(os, "O_DIRECTORY"), reason="平台不支援目錄 fsync"
)
def test_descriptor_is_closed_even_when_fsync_fails(tmp_path, monkeypatch):
    closed: list[int] = []
    real_close = os.close
    monkeypatch.setattr(os, "close", lambda fd: (closed.append(fd), real_close(fd)))

    def boom(_fd):
        raise OSError("fsync failed")

    monkeypatch.setattr(os, "fsync", boom)

    with pytest.raises(OSError):
        fs_utils.fsync_directory(tmp_path)

    assert len(closed) == 1


def test_is_a_noop_where_directories_cannot_be_opened(tmp_path, monkeypatch):
    """Windows（或沒有 ``O_DIRECTORY`` 的平台）直接略過，不開檔、不拋錯。"""
    monkeypatch.setattr(fs_utils.os, "name", "nt")

    def forbidden(*_a, **_k):
        raise AssertionError("不應嘗試開啟目錄")

    monkeypatch.setattr(fs_utils.os, "open", forbidden)

    fs_utils.fsync_directory(tmp_path)
