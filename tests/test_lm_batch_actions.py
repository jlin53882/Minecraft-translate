from translation_tool.core.lm_batch_actions import BatchAction, decide_batch_action


def test_error_to_action_decision_is_pure_and_covers_key_health_paths():
    assert decide_batch_action("model_missing").action is BatchAction.NEXT_MODEL
    assert (
        decide_batch_action("key_forbidden", has_alternative_key=True).action
        is BatchAction.ROTATE_KEY
    )
    assert (
        decide_batch_action("key_forbidden", has_alternative_key=False).action
        is BatchAction.FAIL
    )
    assert (
        decide_batch_action("rate_limited", quota_kind="rpm").action
        is BatchAction.RETRY_SAME_KEY
    )
    assert (
        decide_batch_action(
            "rate_limited", quota_kind="rpd", has_next_model=False
        ).action
        is BatchAction.EXHAUSTED
    )
    # 同專案模式：RPD 換 key 沒用（即使有其他 key），只看還有沒有下一個模型。
    assert (
        decide_batch_action(
            "rate_limited",
            quota_kind="rpd",
            has_alternative_key=True,
            has_next_model=True,
        ).action
        is BatchAction.NEXT_MODEL
    )
    assert (
        decide_batch_action(
            "rate_limited",
            quota_kind="rpd",
            has_alternative_key=True,
            has_next_model=False,
        ).action
        is BatchAction.EXHAUSTED
    )
    assert (
        decide_batch_action(
            "service_unavailable", quota_kind="overloaded", overload_count=2
        ).action
        is BatchAction.RETRY_SAME_MODEL
    )
    assert (
        decide_batch_action(
            "service_unavailable",
            quota_kind="overloaded",
            overload_count=3,
            has_alternative_key=True,
        ).action
        is BatchAction.ROTATE_KEY
    )
    assert decide_batch_action("deadline_exceeded").action is BatchAction.SHRINK_BATCH
    assert decide_batch_action("timeout").action is BatchAction.SHRINK_BATCH
    assert decide_batch_action("server_error").action is BatchAction.SHRINK_BATCH


def test_invalid_argument_decisions_keep_fatal_and_model_fallback_distinct():
    assert (
        decide_batch_action("invalid_argument", has_failed_precondition=True).action
        is BatchAction.FAIL
    )
    assert (
        decide_batch_action(
            "invalid_argument",
            has_output_token_cap_fallback=True,
            has_next_model=True,
        ).action
        is BatchAction.NEXT_MODEL
    )
    assert decide_batch_action("invalid_argument").action is BatchAction.SHRINK_BATCH


def test_overload_without_an_alternative_key_returns_partial():
    decision = decide_batch_action(
        "service_unavailable",
        quota_kind="overloaded",
        overload_count=3,
        has_alternative_key=False,
    )
    assert decision.action is BatchAction.PARTIAL
