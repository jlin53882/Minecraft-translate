from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
APP_VIEWS = BASE / "app" / "views"


def _read(rel: str) -> str:
    return (BASE / rel).read_text(encoding="utf-8")


def test_views_use_shared_components_and_no_local_styled_card():
    targets = [
        "app/views/translation_view.py",
        "app/views/extractor_view.py",
        "app/views/lm_view.py",
        "app/views/merge_view.py",
    ]

    companions = {"app/views/merge_view.py": ["app/views/merge/merge_widgets.py"]}
    for rel in targets:
        src = _read(rel) + "".join(_read(c) for c in companions.get(rel, []))
        # 共用卡片：舊的 styled_card 或新的 kit.section_card（重新設計後逐頁改用 kit）
        assert (
            "styled_card(" in src
            or "section_card(" in src
            or "build_settings_panel(" in src
        ), f"{rel} should use a shared card component"
        assert "def _styled_card(" not in src, (
            f"{rel} should not keep local _styled_card"
        )


def test_config_and_rules_use_shared_buttons():
    """設定 / 規則頁的按鈕要走共用元件（舊 primary_button 或新 kit.button），不可各自拼樣式。"""
    config_src = _read("app/views/config/config_form.py")
    rules_src = _read("app/views/rules_view.py") + _read(
        "app/views/rules/rules_widgets.py"
    )

    assert "primary_button(" in config_src or "kit.button(" in config_src
    assert "kit.button(" in rules_src or (
        "primary_button(" in rules_src and "secondary_button(" in rules_src
    )


def test_cache_view_is_primary_entry_only():
    """cache_view.py 保持主實作，且總覽 controls 使用新的 UI kit。"""

    entry_src = _read("app/views/cache_view.py")

    assert "from app.ui.components import" not in entry_src
    assert "self.btn_reload_all = kit.button(" in entry_src
    assert (
        'self.btn_refresh_stats = kit.button(\n            "刷新統計",\n            "secondary"'
        in entry_src
    )


def test_cache_overview_is_split_to_panel_module():
    entry_src = _read("app/views/cache_manager/cache_view_overview.py")
    assert (
        "from app.views.cache_manager.cache_overview_panel import build_overview_page"
        in entry_src
    )
    assert "return build_overview_page(" in entry_src


def test_cache_related_modules_are_grouped_under_cache_manager():
    """Cache 相關模組應集中在 cache_manager 資料夾，避免 views 根目錄混亂。"""

    assert not (APP_VIEWS / "cache_manager" / "cache_view_impl.py").exists()
    assert not (APP_VIEWS / "cache_manager" / "cache_controller.py").exists()
    assert not (APP_VIEWS / "cache_manager" / "cache_presenter.py").exists()
    assert not (APP_VIEWS / "cache_manager" / "cache_types.py").exists()
    assert (APP_VIEWS / "cache_manager" / "cache_overview_panel.py").exists()
    assert (APP_VIEWS / "cache_manager" / "cache_log_panel.py").exists()
    assert not (APP_VIEWS / "cache_manager" / "cache_shared_widgets.py").exists()

    assert not (APP_VIEWS / "cache_controller.py").exists()
    assert not (APP_VIEWS / "cache_presenter.py").exists()
    assert not (APP_VIEWS / "cache_types.py").exists()
