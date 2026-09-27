def test_load_config_cache_invalidates_on_file_change(tmp_path, monkeypatch):
    """B9：檔案未變時使用快取，修改後重新讀取；回傳值可安全修改。"""
    import json as _json
    import os as _os

    from translation_tool.utils import config_manager as cm

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        _json.dumps({"translator": {"output_dir_name": "A"}}), encoding="utf-8"
    )
    cm.clear_config_cache()

    first = cm.load_config(cfg_path)
    assert first["translator"]["output_dir_name"] == "A"
    first["translator"]["output_dir_name"] = "mutated"
    assert cm.load_config(cfg_path)["translator"]["output_dir_name"] == "A"
    assert cm.load_config_shared(cfg_path) is cm.load_config_shared(cfg_path)

    cfg_path.write_text(
        _json.dumps({"translator": {"output_dir_name": "BB"}}), encoding="utf-8"
    )
    st = cfg_path.stat()
    _os.utime(cfg_path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert cm.load_config(cfg_path)["translator"]["output_dir_name"] == "BB"
