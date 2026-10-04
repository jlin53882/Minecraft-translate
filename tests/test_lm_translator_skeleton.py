"""共同 translator skeleton 的流程 contract 測試。"""


def test_skeleton_forwards_common_loop_contract(monkeypatch):
    from translation_tool.core import lm_translator_skeleton as skeleton
    from translation_tool.core.lm_translator_shared_loop import TranslateLoopResult

    calls: dict[str, object] = {}
    expected = TranslateLoopResult(
        status="DONE",
        processed=1,
        total=1,
        completed_calls=1,
        elapsed_sec=0.1,
        exhausted=False,
    )

    def fake_loop(items, **kwargs):
        calls["items"] = items
        calls.update(kwargs)
        return expected

    monkeypatch.setattr(skeleton, "translate_items_with_cache_loop", fake_loop)

    def translated(batch, total):
        return batch, "AUTO"

    hooks = skeleton.TranslatorHooks(
        on_translated_item=lambda _item: None,
        on_batch_flushed=lambda: None,
        on_batch_checkpoint=lambda _state: None,
        on_progress=lambda _p, _msg, _eta: None,
    )
    items = [{"path": "a", "source_text": "A", "text": "A"}]

    result = skeleton.run_translator_skeleton(
        items,
        translate_batch_smart=translated,
        total_for_smart=1,
        write_new_cache=False,
        hooks=hooks,
    )

    assert result is expected
    assert calls["items"] == items
    assert calls["translate_batch_smart"] is translated
    assert calls["total_for_smart"] == 1
    assert calls["write_new_cache"] is False
    assert calls["on_translated_item"] is hooks.on_translated_item
    assert calls["on_batch_flushed"] is hooks.on_batch_flushed
    assert calls["on_batch_checkpoint"] is hooks.on_batch_checkpoint
    assert calls["on_progress"] is hooks.on_progress
