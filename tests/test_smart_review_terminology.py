from __future__ import annotations

from types import SimpleNamespace

from tools.evaluate_smart_review_terminology import (
    _evaluation_state,
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
            "expected_terms": [{"source": "Phenolic Resin", "accepted": ["酚醛樹脂"]}]
        },
        ("lang", "demo", "wrong", "Phenolic Resin is useful"): {
            "expected_terms": [{"source": "Phenolic Resin", "accepted": ["酚醛樹脂"]}]
        },
        ("lang", "demo", "abstain", "Phenolic Resin is useful"): {
            "expected_terms": [{"source": "Phenolic Resin", "accepted": ["酚醛樹脂"]}]
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
    assert summary["model_term_accuracy_on_emitted"] == 0.5
    assert summary["corrected"] == 1
    assert summary["false_revision"] == 1
    assert summary["abstained_or_no_output"] == 1


def test_provider_failure_is_unavailable_instead_of_zero_accuracy():
    candidate = _candidate("failed", "樹脂", output=None)
    labels = {
        ("lang", "demo", "failed", "Phenolic Resin is useful"): {
            "expected_terms": [{"source": "Phenolic Resin", "accepted": ["酚醛樹脂"]}]
        }
    }

    available, reason = _evaluation_state(
        {"status": "partial", "valid_count": 0, "request_error_count": 1},
        [candidate],
    )
    summary = _manual_terminology_summary(
        [candidate], labels, evaluation_available=available
    )

    assert available is False
    assert reason == "provider requests failed (1)"
    assert summary["evaluation_available"] is False
    assert summary["model_terminology_accuracy_on_emitted"] is None
    assert summary["baseline_terminology_correct"] == 0


def test_unfinished_run_is_unavailable_instead_of_zero_accuracy():
    available, reason = _evaluation_state(
        {"status": "running", "valid_count": 0}, [_candidate("running", "樹脂")]
    )

    assert available is False
    assert reason == "run is not finished (status=running)"
