from pathlib import Path


BASE = Path(__file__).resolve().parents[1]
APP_VIEWS = BASE / "app" / "views"


def _read(rel: str) -> str:
    return (BASE / rel).read_text(encoding="utf-8")


def test_main_uses_shared_view_wrapper():
    src = _read("main.py")
    assert "from app.ui.view_wrapper import wrap_view" in src
    assert "create_view_wrapper(" not in src


def test_views_use_shared_components_and_no_local_styled_card():
    targets = [
        "app/views/translation_view.py",
        "app/views/extractor_view.py",
        "app/views/lm_view.py",
        "app/views/merge_view.py",
    ]

    for rel in targets:
        src = _read(rel)
        assert "styled_card(" in src, f"{rel} should use styled_card"
        assert "def _styled_card(" not in src, f"{rel} should not keep local _styled_card"


def test_config_and_rules_use_shared_buttons():
    config_src = _read("app/views/config_view.py")
    rules_src = _read("app/views/rules_view.py")

    assert "from app.ui.components import primary_button" in config_src
    assert "primary_button(" in config_src

    assert "from app.ui.components import primary_button, secondary_button" in rules_src
    assert "primary_button(" in rules_src
    assert "secondary_button(" in rules_src


def test_cache_view_is_primary_entry_only():
    """cache_view.py 保持主實作，避免多餘的 impl 檔案。"""

    entry_src = _read("app/views/cache_view.py")

    assert "from app.ui.components import primary_button, secondary_button" in entry_src
    assert "self.btn_reload_all = primary_button(" in entry_src
    assert "self.btn_refresh_stats = secondary_button(" in entry_src


def test_cache_overview_is_split_to_panel_module():
    entry_src = _read("app/views/cache_view.py")
    assert "from app.views.cache_manger.cache_overview_panel import build_overview_page" in entry_src
    assert "return build_overview_page(" in entry_src


def test_cache_related_modules_are_grouped_under_cache_manger():
    """Cache 相關模組應集中在 cache_manger 資料夾，避免 views 根目錄混亂。"""

    assert not (APP_VIEWS / "cache_manger" / "cache_view_impl.py").exists()
    assert (APP_VIEWS / "cache_manger" / "cache_controller.py").exists()
    assert (APP_VIEWS / "cache_manger" / "cache_presenter.py").exists()
    assert (APP_VIEWS / "cache_manger" / "cache_types.py").exists()
    assert (APP_VIEWS / "cache_manger" / "cache_overview_panel.py").exists()
    assert (APP_VIEWS / "cache_manger" / "cache_log_panel.py").exists()
    assert (APP_VIEWS / "cache_manger" / "cache_shared_widgets.py").exists()

    assert not (APP_VIEWS / "cache_controller.py").exists()
    assert not (APP_VIEWS / "cache_presenter.py").exists()
    assert not (APP_VIEWS / "cache_types.py").exists()
