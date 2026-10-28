"""流水線 LM 翻譯的即時批次進度契約。"""

from types import SimpleNamespace


def test_directory_lm_progress_is_streamed_with_shared_batch_plan(monkeypatch):
    from translation_tool.core import lm_translator

    updates: list[dict] = []
    events: list[dict] = []
    items = [
        {
            "path": f"item.{index}",
            "text": f"Text {index}",
            "source_text": f"Text {index}",
            "cache_type": "lang",
        }
        for index in range(4)
    ]

    monkeypatch.setattr(lm_translator, "_note_directory_checkpoint", lambda *a, **k: None)
    monkeypatch.setattr(lm_translator, "_plan_directory_batches", lambda _items: 2)
    monkeypatch.setattr(
        lm_translator,
        "load_config",
        lambda: {"lm_translator": {"provider": "gemini", "token_budget_enabled": False}},
    )
    monkeypatch.setattr(lm_translator, "flush_write_back", lambda *_a, **_k: None)
    monkeypatch.setattr(lm_translator, "clear_checkpoint", lambda: None)

    observed_before_skeleton_returns = []

    def fake_skeleton(_items, *, hooks, **_kwargs):
        assert hooks.batch_total == 2
        hooks.on_progress(0.0, "🚀 開始", 0.0)
        observed_before_skeleton_returns.append(list(updates))
        hooks.on_event(
            {
                "kind": "batch_start",
                "operation": "translate",
                "provider": "gemini",
                "model": "model-pool",
                "batch_index": 1,
                "batch_total": 2,
                "batch_size": 2,
                "expected_count": 2,
            }
        )
        hooks.on_progress(0.5, "✅ 批次完成 (lang)", 0.0)
        hooks.on_progress(1.0, "🏁 任務結束", 0.0)
        return SimpleNamespace(
            status="DONE",
            processed=4,
            metrics={"batch_count": 2},
        )

    monkeypatch.setattr(lm_translator, "run_translator_skeleton", fake_skeleton)

    result, pending, _translation_log, processed = lm_translator._run_directory_translation(
        remaining=items,
        file_cache={},
        root=lm_translator.Path("."),
        out_root=lm_translator.Path("."),
        export_lang=False,
        should_cancel=None,
        write_new_cache=False,
        input_dir=".",
        items_to_translate=items,
        cache_hit_count=0,
        checkpoint_fingerprint="test",
        total=len(items),
        update_sink=updates.append,
        event_sink=events.append,
        event_task_id="pipeline-task",
    )

    assert observed_before_skeleton_returns and observed_before_skeleton_returns[0]
    assert pending == []
    assert processed == 4
    assert result.metrics["planned_batches"] == 2
    assert events[0]["batch_total"] == 2
    assert "預估約 2 批" in updates[0]["log"]
    assert any("第 1 / 約 2 批" in update["log"] for update in updates)


def test_directory_work_reports_actual_batches_in_final_log(monkeypatch):
    from translation_tool.core import lm_translator

    updates: list[dict] = []
    items = [{"path": "item.1", "cache_type": "lang"}]
    result = SimpleNamespace(
        status="DONE",
        processed=1,
        metrics={"planned_batches": 2, "batch_count": 3},
    )

    monkeypatch.setattr(lm_translator, "_cache_saving_enabled", lambda: False)
    monkeypatch.setattr(
        lm_translator,
        "_run_directory_translation",
        lambda **_kwargs: (result, [], [], 1),
    )

    yielded = list(
        lm_translator._translate_directory_work(
            items_to_translate=items,
            cache_hit_count=0,
            file_cache={},
            root=lm_translator.Path("."),
            out_root=lm_translator.Path("."),
            export_lang=False,
            should_cancel=None,
            write_new_cache=False,
            input_dir=".",
            checkpoint_fingerprint="test",
            db_ctx=None,
            db_choice=(False, ""),
            frozen_settings=None,
            update_sink=updates.append,
        )
    )

    assert yielded == []
    assert "共送出 3 批（開始預估 2 批）" in updates[-1]["log"]
