from pathlib import Path

import orjson as json

from translation_tool.utils import cache_shards


def test_get_active_shard_path_backfills_latest_shard_id(tmp_path: Path):
    type_dir = tmp_path / "lang"
    type_dir.mkdir(parents=True, exist_ok=True)

    (type_dir / "lang_00001.json").write_bytes(json.dumps({"a": 1}))
    (type_dir / "lang_00003.json").write_bytes(json.dumps({"b": 2}))

    target = cache_shards._get_active_shard_path(
        type_dir=type_dir,
        cache_type="lang",
        active_shard_file=".active",
    )

    assert target.name == "lang_00003.json"
    assert (type_dir / ".active").read_text(encoding="utf-8").strip() == "00003"


def test_rotate_shard_if_needed_when_full(tmp_path: Path):
    type_dir = tmp_path / "lang"
    type_dir.mkdir(parents=True, exist_ok=True)
    (type_dir / ".active").write_text("00007", encoding="utf-8")

    rotated = cache_shards._rotate_shard_if_needed(
        type_dir=type_dir,
        cache_type="lang",
        data={"k1": {"dst": "v1"}, "k2": {"dst": "v2"}},
        rolling_shard_size=2,
        active_shard_file=".active",
    )

    assert rotated is True
    assert (type_dir / ".active").read_text(encoding="utf-8").strip() == "00008"


def test_save_entries_force_new_shard_rotates_before_write(tmp_path: Path):
    type_dir = tmp_path / "lang"
    type_dir.mkdir(parents=True, exist_ok=True)

    (type_dir / ".active").write_text("00001", encoding="utf-8")
    (type_dir / "lang_00001.json").write_bytes(json.dumps({"old": {"dst": "舊"}}))

    cache_shards._save_entries_to_active_shards(
        type_dir=type_dir,
        cache_type="lang",
        entries={
            "new1": {"src": "a", "dst": "甲"},
            "new2": {"src": "b", "dst": "乙"},
        },
        rolling_shard_size=10,
        active_shard_file=".active",
        force_new_shard=True,
    )

    assert (type_dir / ".active").read_text(encoding="utf-8").strip() == "00002"

    shard1 = json.loads((type_dir / "lang_00001.json").read_bytes())
    shard2 = json.loads((type_dir / "lang_00002.json").read_bytes())

    assert "old" in shard1
    assert set(shard2.keys()) == {"new1", "new2"}


def test_write_json_atomic_tmp_to_target(tmp_path: Path):
    path = tmp_path / "lang" / "lang_00001.json"

    cache_shards._write_json_atomic(path, {"k": {"src": "s", "dst": "d"}})

    assert path.exists()
    assert not path.with_suffix(".tmp").exists()
    assert json.loads(path.read_bytes()) == {"k": {"src": "s", "dst": "d"}}
