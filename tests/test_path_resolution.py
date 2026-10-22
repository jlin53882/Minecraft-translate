import json

from translation_tool.utils import cache_manager, config_manager, text_processor


def test_load_config_uses_project_root_not_cwd(tmp_path, monkeypatch):
    fake_root = tmp_path / "project_root"
    fake_root.mkdir(parents=True)
    (fake_root / "config.json").write_text(
        json.dumps({"translator": {"cache_directory": "cache_root_from_config"}}, ensure_ascii=False),
        encoding="utf-8",
    )

    other_cwd = tmp_path / "other_cwd"
    other_cwd.mkdir(parents=True)

    monkeypatch.chdir(other_cwd)
    monkeypatch.setattr(config_manager, "get_project_root", lambda: fake_root)
    monkeypatch.setattr(config_manager, "PROJECT_ROOT", fake_root)
    monkeypatch.setattr(config_manager, "CONFIG_PATH", fake_root / "config.json")

    cfg = config_manager.load_config()
    assert cfg["translator"]["cache_directory"] == "cache_root_from_config"


def test_cache_root_uses_project_root_not_cwd(tmp_path, monkeypatch):
    fake_root = tmp_path / "project_root"
    fake_root.mkdir(parents=True)
    other_cwd = tmp_path / "other_cwd"
    other_cwd.mkdir(parents=True)

    monkeypatch.chdir(other_cwd)
    monkeypatch.setattr(cache_manager, "load_config", lambda: {"translator": {"cache_directory": "cache_root_from_config"}})
    monkeypatch.setattr(cache_manager, "resolve_project_path", lambda p: fake_root / p)

    cache_root = cache_manager._get_cache_root()
    assert cache_root == fake_root / "cache_root_from_config"


def test_replace_rules_relative_path_resolves_to_project_root(tmp_path, monkeypatch):
    fake_root = tmp_path / "project_root"
    fake_root.mkdir(parents=True)
    rules_path = fake_root / "replace_rules.json"
    rules_path.write_text(
        json.dumps([
            {"from": "abcdef", "to": "A"},
            {"from": "abc", "to": "B"},
        ], ensure_ascii=False),
        encoding="utf-8",
    )

    other_cwd = tmp_path / "other_cwd"
    other_cwd.mkdir(parents=True)

    monkeypatch.chdir(other_cwd)
    monkeypatch.setattr(text_processor, "resolve_project_path", lambda p: fake_root / p)

    rules = text_processor.load_replace_rules("replace_rules.json")
    assert [r["from"] for r in rules] == ["abcdef", "abc"]
