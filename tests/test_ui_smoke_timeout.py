"""Deterministic timeout regression for the UI smoke startup probe."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.ui_smoke import _wait_for_server


class _LiveProcess:
    """Fake process that remains alive during failed probes."""

    def poll(self):
        return None


def test_wait_for_server_times_out_after_persistent_probe_failures():
    """Failed HTTP probes must still reach the shared deadline path."""
    now = 0.0
    sleeps: list[float] = []
    probes = 0

    def monotonic():
        return now

    def sleep(seconds):
        nonlocal now
        sleeps.append(seconds)
        now += seconds

    def urlopen(_url, timeout):
        nonlocal probes
        probes += 1
        raise OSError("server is not ready")

    with pytest.raises(TimeoutError):
        _wait_for_server(
            _LiveProcess(),
            "http://127.0.0.1:12345",
            Path("server.log"),
            timeout_seconds=1.0,
            poll_interval=0.2,
            monotonic=monotonic,
            sleep=sleep,
            urlopen=urlopen,
        )

    assert probes > 0
    assert sleeps
    assert all(seconds == 0.2 for seconds in sleeps)
    assert now >= 1.0
