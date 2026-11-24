def test_load_config_cache_invalidates_on_file_change(tmp_path, monkeypatch):
    """B9：檔案未變時使用快取，修改後重新讀取；回傳值可安全修改。"""
    import json as _json
    import os as _os

    from translation_tool.utils import config_manager as cm

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        _json.dumps({"translator": {"replace_rules_path": "A"}}), encoding="utf-8"
    )
    cm.clear_config_cache()

    first = cm.load_config(cfg_path)
    assert first["translator"]["replace_rules_path"] == "A"
    first["translator"]["replace_rules_path"] = "mutated"
    assert cm.load_config(cfg_path)["translator"]["replace_rules_path"] == "A"
    assert cm.load_config_shared(cfg_path) is cm.load_config_shared(cfg_path)

    cfg_path.write_text(
        _json.dumps({"translator": {"replace_rules_path": "BB"}}), encoding="utf-8"
    )
    st = cfg_path.stat()
    _os.utime(cfg_path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert cm.load_config(cfg_path)["translator"]["replace_rules_path"] == "BB"


def test_legacy_removed_keys_in_user_config_are_ignored_and_preserved(tmp_path):
    """舊 config.json 殘留已移除的設定鍵：載入與同步不報錯，也不會被補回或清掉。"""
    import json as _json

    from translation_tool.utils import config_manager as cm

    legacy = {
        "translator": {
            "output_dir_name": "my_out",
            "custom_translator_folder": "my_custom",
            "replace_rules_path": "rules.json",
        },
        "ftb_translator": {"output_dir_name": "my_ftb_out"},
    }
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(_json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    cm.clear_config_cache()

    loaded = cm.load_config(cfg_path)
    assert loaded["translator"]["replace_rules_path"] == "rules.json"
    # 範本與預設值都沒有這些鍵，使用者的值原樣留在合併結果裡（不生效）
    assert loaded["translator"]["output_dir_name"] == "my_out"

    cm.sync_missing_config_keys(cfg_path)
    synced = _json.loads(cfg_path.read_text(encoding="utf-8"))
    assert synced["translator"]["output_dir_name"] == "my_out"
    assert synced["ftb_translator"] == {"output_dir_name": "my_ftb_out"}

    # 沒有舊鍵的設定，載入結果不會憑空補出這些鍵
    clean_path = tmp_path / "clean.json"
    clean_path.write_text("{}", encoding="utf-8")
    cm.clear_config_cache()
    clean = cm.load_config(clean_path)
    assert "output_dir_name" not in clean["translator"]
    assert "custom_translator_folder" not in clean["translator"]
    assert "ftb_translator" not in clean
    cm.clear_config_cache()
