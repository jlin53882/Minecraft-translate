"""修復審查（repair review）狀態詞彙：集中定義、不得在使用端寫裸字面值。"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from translation_tool.translation_db.repair_status import (
    REPAIR_REVIEW_STATUS_LABELS,
    RepairApplyOutcome,
    RepairReviewStatus,
    RepairStaleReason,
    split_stale_reason,
    stale_outcome,
    status_after_apply,
)

ROOT = Path(__file__).resolve().parents[1]
CONSUMERS = (
    "app/views/moddb/repair_reviewer.py",
    "app/services_impl/moddb_repair_review_store.py",
    "app/services_impl/moddb_retranslate_service.py",
)
# 這些值必須經由 repair_status 的 enum 取用；"cancelled" 是批次／操作狀態，不屬於修復審查。
FORBIDDEN_LITERALS = {
    str(member) for enum in (RepairReviewStatus, RepairApplyOutcome) for member in enum
} | {"stale:"}


def _bare_literals(relative: str) -> list[tuple[int, str]]:
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
            ):
                docstrings.add(id(body[0].value))
    found = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
            and node.value in FORBIDDEN_LITERALS
        ):
            found.append((node.lineno, node.value))
    return found


@pytest.mark.parametrize("relative", CONSUMERS)
def test_consumers_use_enums_not_bare_literals(relative):
    assert _bare_literals(relative) == [], (
        f"{relative} 有修復審查狀態的裸字面值；請改用 "
        "translation_tool.translation_db.repair_status 的 enum"
    )


def test_values_match_persisted_strings():
    assert {s.value for s in RepairReviewStatus} == {
        "pending",
        "kept_old",
        "applied",
        "stale",
        "invalid",
    }
    assert RepairApplyOutcome.ALREADY_APPLIED == "already_applied"
    assert RepairStaleReason.UNCLASSIFIED == ""


def test_every_status_has_a_label():
    assert set(REPAIR_REVIEW_STATUS_LABELS) == set(RepairReviewStatus)


def test_stale_outcome_round_trip():
    assert stale_outcome("") == "stale"
    assert stale_outcome(RepairStaleReason.CHANGED) == "stale:changed"
    assert split_stale_reason("stale:changed") == RepairStaleReason.CHANGED
    assert split_stale_reason("stale") == ""


def test_status_after_apply():
    assert status_after_apply("applied") is RepairReviewStatus.APPLIED
    assert status_after_apply("already_applied") is RepairReviewStatus.APPLIED
    assert status_after_apply("stale:missing") is RepairReviewStatus.STALE
    assert status_after_apply("stale") is RepairReviewStatus.STALE
