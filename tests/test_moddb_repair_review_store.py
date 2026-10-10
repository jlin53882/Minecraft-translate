from app.services_impl import moddb_repair_review_store as store


def test_review_store_keeps_results_after_two_hundred_and_restores_drafts(tmp_path):
    db_path = tmp_path / "mod.db"
    run_id = "run-1"
    path = store.create_run(db_path, run_id)
    for index in range(205):
        store.append_item(
            path, run_id, {"key": f"key.{index}", "ai_translation": "草稿"}
        )

    assert store.run_count(path, run_id) == 205
    item = store.load_item(path, run_id, 201)
    assert item["key"] == "key.200"
    assert store.save_review_state(path, run_id, 201, "invalid", "修正草稿")
    restored = store.load_item(path, run_id, 201)
    assert restored["review_status"] == "invalid"
    assert restored["draft"] == "修正草稿"
    assert store.indices(path, run_id, "pending") == list(range(1, 201)) + [
        202,
        203,
        204,
        205,
    ]
    assert store.status_counts(path, run_id)["invalid"] == 1
