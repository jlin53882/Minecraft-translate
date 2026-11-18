"""審核對話框的模組選單：只列有結果的模組。"""

from __future__ import annotations

from app.views.moddb.result_mod_options import ResultModOptions


def test_full_scan_needed_only_for_single_mod_with_unknown_scope():
    options = ResultModOptions()
    assert (
        options.needs_full_scan(("1.21.1",), None) is False
    )  # 不限模組本身就是完整結果
    assert options.needs_full_scan(("1.21.1",), "alpha") is True
    options.update(("1.21.1",), None, has_results=True, full_mods=["beta", "alpha"])
    assert options.needs_full_scan(("1.21.1",), "alpha") is False
    assert options.needs_full_scan(("1.20.1",), "alpha") is True


def test_single_mod_result_updates_list_but_keeps_selection_visible():
    options = ResultModOptions()
    key = ("1.21.1",)
    options.update(key, None, has_results=True, full_mods=["alpha", "beta"])
    assert options.options(None) == ["alpha", "beta"]

    options.update(key, "alpha", has_results=False)  # 套用後 alpha 已無結果
    assert options.options("alpha") == ["alpha", "beta"]  # 仍選著：保留顯示
    assert options.options(None) == ["beta"]  # 換回全部模組：不再列出

    options.update(("other",), "beta", has_results=False)  # 其他範圍的結果不影響
    assert options.options(None) == ["beta"]
