"""智慧校對審查頁的非同步結果亂序：舊請求慢回來時不得覆蓋新的狀態。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.views.moddb import smart_review_candidate_loader as loader_module
from app.views.moddb import smart_review_reviewer as reviewer_module
from app.views.moddb.smart_review_context import SmartReviewContext
from app.views.moddb.smart_review_reviewer import SmartReviewReviewer
from app.views.moddb.smart_review_reviewer_state import SmartReviewReviewerState
from tests.test_smart_review_ui import _candidate, _FakeService, _sync_page


class _DeferredQueue:
    """攔截背景工作：工作立刻算出結果（模擬背景執行緒），但結果由測試決定何時送回 UI。"""

    def __init__(self) -> None:
        """建立空佇列。"""
        self.items: list[SimpleNamespace] = []

    def run_background(self, work, on_done, *, still_valid=None, name="", **_kwargs):
        """記錄工作與其結果；不立刻交給 ``on_done``。"""
        try:
            result, error = work(), None
        except Exception as exc:  # noqa: BLE001 - mirrors production error capture
            result, error = None, str(exc)
        self.items.append(
            SimpleNamespace(
                name=name,
                result=result,
                error=error,
                on_done=on_done,
                still_valid=still_valid,
            )
        )
        return True

    def named(self, name: str) -> list[SimpleNamespace]:
        """依名稱取出尚未送回的項目。"""
        return [item for item in self.items if item.name == name]

    def deliver(self, item: SimpleNamespace) -> bool:
        """比照 ``SmartReviewContext.run_background``：過期就丟棄，回傳是否真的送達。"""
        if item.still_valid is not None and not item.still_valid():
            return False
        item.on_done(item.result, item.error)
        return True


@pytest.fixture
def queue():
    """攔截背景工作的佇列。"""
    return _DeferredQueue()


@pytest.fixture
def reviewer(tmp_path, monkeypatch, queue):
    """建立結果由測試控制送回時機的審查面板。"""
    database = SimpleNamespace(path=tmp_path / "async.db", priority=(0,))
    ctx = SmartReviewContext(_sync_page(), lambda: database, lambda *_a: None)
    ctx.run_background = queue.run_background
    rows = [
        _candidate(1, "ready", suggested_zh_tw="一"),
        _candidate(2, "ready", suggested_zh_tw="二"),
        _candidate(3, "ready", suggested_zh_tw="三"),
        _candidate(4, "applied"),
        _candidate(5, "rejected"),
    ]
    fake = _FakeService(rows)
    monkeypatch.setattr(
        loader_module, "moddb_smart_review_read_session", fake.read_session
    )
    monkeypatch.setattr(
        loader_module,
        "realign_moddb_smart_review_candidate_from_path",
        lambda *_a, **_k: "unchanged",
    )
    monkeypatch.setattr(
        reviewer_module,
        "show_snack",
        lambda page, msg, *_a, **_k: page.snacks.append(msg),
    )
    panel = SmartReviewReviewer(ctx, on_changed=lambda: None)
    panel.fake = fake
    # 讓測試能直接設定 state、從第一筆開始載入
    panel.state.run_id = "run-1"
    return panel


def test_state_tokens_expire_older_requests():
    """每次登記新請求都會讓舊序號過期。"""
    state = SmartReviewReviewerState()
    first = state.begin_request()
    assert state.is_current(first)
    second = state.begin_request()
    assert not state.is_current(first)
    assert state.is_current(second)


def test_older_position_result_cannot_overwrite_newer_one(reviewer, queue):
    """連續載入兩個位置：新結果先到，舊結果之後到也必須被丟棄。"""
    state = reviewer.state
    state.position = 0
    reviewer.load_position()
    state.position = 2
    reviewer.load_position()
    old, new = queue.named("載入智慧校對候選")

    assert queue.deliver(new) is True
    assert state.row.ordinal == 3
    assert queue.deliver(old) is False

    assert state.row.ordinal == 3
    assert state.position == 2
    assert reviewer.position_input.value == "3"
    assert reviewer.draft.value == "三"


def test_older_position_result_is_dropped_when_it_arrives_first(reviewer, queue):
    """舊結果先到、新結果後到：舊結果同樣不得顯示（它已被新請求取代）。"""
    state = reviewer.state
    state.position = 0
    reviewer.load_position()
    state.position = 1
    reviewer.load_position()
    old, new = queue.named("載入智慧校對候選")

    assert queue.deliver(old) is False
    assert state.row is None  # 新請求回來前不顯示任何舊結果
    assert queue.deliver(new) is True

    assert state.row.ordinal == 2
    assert state.busy is False


def test_switching_filter_discards_result_of_previous_filter(reviewer, queue):
    """切換篩選後，上一個篩選慢回來的候選不得蓋掉新篩選的畫面。"""
    state = reviewer.state
    state.counts = {"ready": 3, "applied": 1, "rejected": 1}
    state.status_filter = "ready"
    state.position = 0
    reviewer.load_position()
    state.status_filter = "applied"
    state.position = 0
    reviewer.load_position()
    ready_load, applied_load = queue.named("載入智慧校對候選")

    assert queue.deliver(applied_load) is True
    assert queue.deliver(ready_load) is False

    assert state.status_filter == "applied"
    assert state.row.status == "applied"
    assert state.total == 1


def test_older_run_load_cannot_replace_newer_run(reviewer, queue):
    """連開兩個批次：先開的批次慢回來時不得覆蓋後開的批次資訊與篩選。"""
    reviewer.load_run("run-old")
    reviewer.load_run("run-new")
    old, new = queue.named("載入智慧校對批次")

    assert queue.deliver(new) is True
    assert queue.deliver(old) is False

    assert reviewer.state.run_id == "run-new"
    assert reviewer.state.run_info["id"] == "run-new"


def test_stale_preload_is_dropped_after_filter_change(reviewer, queue):
    """預載中途切換篩選：舊篩選的預載結果不得進入快取。"""
    state = reviewer.state
    state.status_filter = "all"
    state.position = 3  # 已套用的筆需要對齊快照，所以相鄰的 rejected 不會被預載進快取
    reviewer.load_position()
    queue.deliver(queue.named("載入智慧校對候選")[0])
    (preload,) = queue.named("預載智慧校對候選")

    state.status_filter = "applied"
    state.position = 0
    reviewer.load_position()

    assert queue.deliver(preload) is False
    queue.deliver(queue.named("載入智慧校對候選")[-1])
    assert state.row.status == "applied"
    assert reviewer.loader.take("run-1", None, reviewer.ctx.db_identity(), 2) is None


def test_navigation_uses_preloaded_neighbor_without_new_query(reviewer, queue):
    """預載完成後換筆直接顯示鄰近候選，並讓尚未送回的舊請求失效。"""
    state = reviewer.state
    state.position = 0
    reviewer.load_position()
    queue.deliver(queue.named("載入智慧校對候選")[0])
    for item in queue.named("預載智慧校對候選"):
        queue.deliver(item)
    loads = len(queue.named("載入智慧校對候選"))

    reviewer._move(1)

    assert state.row.ordinal == 2
    assert len(queue.named("載入智慧校對候選")) == loads  # 沒有新的載入請求
    (preload,) = queue.named("預載智慧校對候選")[-1:]  # 只排入下一筆的預載
    assert queue.deliver(preload) is True
    assert reviewer.loader.take("run-1", "ready", reviewer.ctx.db_identity(), 2)
