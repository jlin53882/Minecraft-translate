"""智慧校對狀態機契約：狀態名稱、分組與轉移只由 smart_review_status 定義。"""

import json
import re
from pathlib import Path

import pytest

from translation_tool.translation_db import smart_review, smart_review_history_codec
from translation_tool.translation_db.models import SMART_REVIEW_FILTER_GROUPS
from translation_tool.translation_db.smart_review_history import (
    SmartReviewHistoryStore,
)
from translation_tool.translation_db.smart_review_status import (
    DECISION_STATUS,
    MANUAL_EDIT_STATUSES,
    PROCESSED_STATUSES,
    TRANSITIONS,
    VALID_STATUSES,
    InvalidStatusTransition,
    SmartReviewStatus,
    can_transition,
    coerce_status,
    status_for_decision,
    validate_transition,
)

ROOT = Path(__file__).resolve().parents[1]
S = SmartReviewStatus


def test_enum_values_match_persisted_strings():
    assert {s.value for s in S} == {
        "pending",
        "ready",
        "no_change",
        "needs_context",
        "invalid",
        "request_failed",
        "cancelled",
        "applied",
        "rejected",
        "stale",
        "invalid_format",
    }
    assert S.READY == "ready"
    assert json.dumps({"status": S.READY}) == '{"status": "ready"}'


def test_every_status_has_transition_entry_and_targets_are_known():
    assert set(TRANSITIONS) == set(S)
    for targets in TRANSITIONS.values():
        assert targets <= set(S)


def test_applicable_statuses_match_manual_edit_contract():
    applicable = {s for s in S if can_transition(s, S.APPLIED)}
    assert applicable == MANUAL_EDIT_STATUSES | {S.READY}


def test_only_ready_can_be_rejected():
    assert {s for s in S if can_transition(s, S.REJECTED)} == {S.READY}


def test_pending_is_never_a_transition_target():
    assert all(S.PENDING not in targets for targets in TRANSITIONS.values())


def test_processed_and_valid_groups():
    assert VALID_STATUSES < PROCESSED_STATUSES
    assert S.PENDING not in PROCESSED_STATUSES and S.CANCELLED not in PROCESSED_STATUSES


def test_validate_transition_reports_reason():
    validate_transition("ready", "applied")
    with pytest.raises(InvalidStatusTransition) as exc:
        validate_transition("pending", "applied")
    assert "pending" in exc.value.reason and "applied" in exc.value.reason
    assert not can_transition("unknown", "applied")


def test_decision_mapping_and_coerce():
    assert status_for_decision("suggest_revision") is S.READY
    assert status_for_decision("bogus") is S.INVALID
    assert set(DECISION_STATUS) == set(smart_review.SMART_REVIEW_DECISIONS)
    assert coerce_status(None) is S.PENDING
    assert coerce_status("") is S.PENDING
    assert coerce_status("applied") is S.APPLIED
    assert coerce_status("future_status") == "future_status"


def test_manual_edit_and_filter_groups_derive_from_state_machine():
    assert smart_review.SMART_REVIEW_MANUAL_EDIT_STATUSES is MANUAL_EDIT_STATUSES
    assert set(SMART_REVIEW_FILTER_GROUPS["any"]) == MANUAL_EDIT_STATUSES
    known = {s.value for s in S}
    for group in SMART_REVIEW_FILTER_GROUPS.values():
        assert set(group) <= known


def test_sql_status_literals_are_known_statuses():
    known = {s.value for s in S}
    text = (ROOT / "translation_tool/translation_db/smart_review.py").read_text(
        encoding="utf-8"
    )
    used = set(re.findall(r"smart_review_candidate SET status\s*=\s*'([a-z_]+)'", text))
    assert used and used <= known


@pytest.mark.parametrize(
    "path",
    [
        "translation_tool/translation_db/smart_review.py",
        "translation_tool/translation_db/smart_review_history.py",
        "translation_tool/translation_db/smart_review_history_codec.py",
        "translation_tool/translation_db/smart_review_history_manifest.py",
        "app/views/moddb/smart_review_reviewer.py",
    ],
)
def test_no_bare_status_comparisons(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    names = "|".join(status.value for status in S)
    bare = re.findall(
        rf'(?:status"\)|\.status|\[15\]|\[14\])\s*(?:==|!=)\s*"(?:{names})"', text
    )
    assert bare == []


def test_clear_caches(tmp_path):
    SmartReviewHistoryStore.clear_caches()
    store = SmartReviewHistoryStore(tmp_path / "db.sqlite")
    SmartReviewHistoryStore._recovery_checked.add(store.directory)
    SmartReviewHistoryStore._corrupt_runs[store.directory / "x.json"] = ((0, 0), ())
    other = tmp_path / "other"
    SmartReviewHistoryStore._recovery_checked.add(other)
    SmartReviewHistoryStore.clear_caches(store.directory)
    assert store.directory not in SmartReviewHistoryStore._recovery_checked
    assert other in SmartReviewHistoryStore._recovery_checked
    assert not SmartReviewHistoryStore._corrupt_runs
    SmartReviewHistoryStore.clear_caches()
    assert not SmartReviewHistoryStore._recovery_checked


def test_clear_smart_review_caches():
    smart_review.clear_smart_review_caches()
    smart_review._RECONCILED_HISTORY_HEAD[("a.db", "r1")] = 3
    smart_review._RECONCILED_HISTORY_HEAD[("b.db", "r1")] = 4
    smart_review.clear_smart_review_caches("a.db")
    assert list(smart_review._RECONCILED_HISTORY_HEAD) == [("b.db", "r1")]
    smart_review.clear_smart_review_caches()
    assert not smart_review._RECONCILED_HISTORY_HEAD


def test_history_module_uses_shared_groups():
    assert smart_review_history_codec.PROCESSED_CANDIDATE_STATUSES is PROCESSED_STATUSES
