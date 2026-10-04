import json

from translation_tool.core.lm_translator_skeleton import make_checkpoint_adapter


def test_checkpoint_adapter_persists_minimal_recovery_metadata(tmp_path):
    adapter = make_checkpoint_adapter(
        "md",
        [{"file": "a", "path": "p", "source_text": "hello", "text": "hello"}],
        target="out.json",
    )
    adapter.path = tmp_path / "checkpoint.json"

    adapter(
        {
            "cache_type": "md",
            "processed": 3,
            "total": 8,
            "completed_calls": 1,
            "status": "AUTO",
        }
    )

    payload = json.loads(adapter.path.read_text(encoding="utf-8"))
    assert payload["plugin"] == "md"
    assert payload["target"] == "out.json"
    assert payload["processed"] == 3
    assert payload["total"] == 8
    assert payload["fingerprint"]
    adapter.clear()
    assert not adapter.path.exists()
