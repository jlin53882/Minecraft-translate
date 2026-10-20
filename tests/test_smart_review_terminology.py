from __future__ import annotations

from types import SimpleNamespace

from tools.evaluate_smart_review_terminology import (
    _manual_terminology_summary,
)


def _candidate(
    key: str,
    current: str,
    *,
    output: str | None = None,
    decision: str = "suggest_revision",
):
    return SimpleNamespace(
        kind="lang",
        mod_id="demo",
        key=key,
        en_us="Phenolic Resin is useful",
        current_zh_tw=current,
        status="ready" if output is not None else "needs_context",
        decision=decision,
        suggested_zh_tw=output,
    )


def test_manual_terminology_metrics_distinguish_correction_false_revision_and_abstention():
    labels = {
        ("lang", "demo", "correct", "Phenolic Resin is useful"): {
            "expected_terms": [
                {"source": "Phenolic Resin", "accepted": ["酚醛樹脂"]}
            ]
        },
        ("lang", "demo", "wrong", "Phenolic Resin is useful"): {
            "expected_terms": [
                {"source": "Phenolic Resin", "accepted": ["酚醛樹脂"]}
            ]
        },
        ("lang", "demo", "abstain", "Phenolic Resin is useful"): {
            "expected_terms": [
                {"source": "Phenolic Resin", "accepted": ["酚醛樹脂"]}
            ]
        },
    }
    candidates = [
        _candidate("correct", "樹脂", output="酚醛樹脂很有用"),
        _candidate("wrong", "酚醛樹脂", output="錯誤翻譯"),
        _candidate("abstain", "樹脂"),
    ]

    summary = _manual_terminology_summary(candidates, labels)

    assert summary["labelled_candidates"] == 3
    assert summary["coverage_on_labels"] == 0.6667
    assert summary["model_terminology_correct"] == 1
    assert summary["corrected"] == 1
    assert summary["false_revision"] == 1
    assert summary["abstained_or_no_output"] == 1
