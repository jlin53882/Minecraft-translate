"""審核工具選單的處理函式：資料載入中或清單有錯誤時一律不開啟對話框。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.views.moddb import entry_filters

HANDLERS = (
    "open_batch_replace",
    "open_bulk_review",
    "open_version_diff",
    "open_same_text_review",
    "open_species_scan",
    "open_smart_review",
)
DIALOGS = (
    "BatchReplaceDialog",
    "BulkReviewDialog",
    "VersionDiffDialog",
    "SameTextReviewDialog",
    "SpeciesCandidateDialog",
    "SmartReviewDialog",
)


@pytest.fixture
def spy(monkeypatch):
    shown: list[str] = []
    monkeypatch.setattr(
        entry_filters, "show_snack", lambda _page, text, *_a, **_k: shown.append(text)
    )

    def forbidden(*_a, **_k):
        raise AssertionError("對話框不應該被建立")

    for name in DIALOGS:
        monkeypatch.setattr(entry_filters, name, forbidden)
    return shown


def _panel(**overrides):
    base = {
        "_page": object(),
        "_data_loading": False,
        "_list_error": None,
        "drafts": {},
        "db": lambda: object(),
        "version": "1.21.1",
    }
    return SimpleNamespace(**{**base, **overrides})


@pytest.mark.parametrize("name", HANDLERS)
def test_handlers_refuse_while_data_is_loading(spy, name):
    getattr(entry_filters, name)(_panel(_data_loading=True))
    assert spy == ["條目資料仍在載入，請稍候再操作。"]


@pytest.mark.parametrize("name", HANDLERS)
def test_handlers_refuse_when_the_list_has_an_error(spy, name):
    getattr(entry_filters, name)(_panel(_list_error="日期格式請使用 YYYY-MM-DD"))
    assert len(spy) == 1 and "日期格式請使用 YYYY-MM-DD" in spy[0]
