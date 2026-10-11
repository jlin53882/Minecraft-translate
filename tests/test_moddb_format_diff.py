import flet as ft

from app.services_impl.moddb_format_diff_service import compare_format_diff
from app.views.moddb.format_diff_review import FormatDiffReview
from app.views.moddb.format_diff_viewer import FormatDiffViewer


def test_repeated_tokens_report_counts_without_fake_missing_positions():
    result = compare_format_diff("§7a§7b§7c§7d§7e", "甲§7乙§7丙§7")

    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.token == "§7"
    assert issue.source_count == 5
    assert issue.target_count == 3
    assert issue.direction == "missing"
    assert issue.location_confidence == "unknown"
    assert "缺少 §7 × 2（位置無法可靠判定）" in result.summary_lines
    assert len(issue.source_occurrences) == 5
    assert len(issue.target_occurrences) == 3

    located = compare_format_diff("第一行\n§7第二行", "第一行\n第二行")
    occurrence = located.issues[0].source_occurrences[0]
    assert (occurrence.line, occurrence.column) == (2, 1)


def test_adjacent_minecraft_tokens_and_patchouli_tokens_use_the_shared_scanner():
    result = compare_format_diff(
        "§n粗體§r§7$(br)內容 %s %1$d 100%",
        "粗體§r$(br)內容 %s %1$d 100%",
    )

    assert [(issue.token, issue.direction) for issue in result.issues] == [
        ("§7", "missing"),
        ("§n", "missing"),
    ]
    assert all(issue.category == "minecraft" for issue in result.issues)
    assert all("位置無法可靠判定" in issue.summary for issue in result.issues)


def test_newline_and_literal_backslash_n_are_distinct_format_tokens():
    result = compare_format_diff("第一行\n第二行\\n", "第一行第二行\n")

    messages = "；".join(result.summary_lines)
    assert "換行數量不一致：原文 1、譯文 1" not in messages
    assert "缺少 字面 \\n × 1（位置無法可靠判定）" in messages


def test_natural_language_changes_do_not_create_a_format_diff():
    result = compare_format_diff("A list of items", "物品清單")

    assert result.has_issues is False
    assert result.summary_lines == ("格式檢查通過。",)


def test_viewer_keeps_markers_out_of_text_and_can_toggle_highlights():
    viewer = FormatDiffViewer()
    result = viewer.render("Use %s", "使用")

    assert result.legacy_messages == ("少了 1 個「%s」",)
    assert viewer.summary.value == "缺少 %s × 1（位置無法可靠判定）"
    assert viewer.box.visible is False

    viewer.row.value = True
    viewer._toggle()

    assert viewer.box.visible is True
    assert "[缺少" not in result.target_text
    assert "使用" in "".join(span.text or "" for span in viewer.tw.spans)


def test_shared_review_renders_same_contract_without_inserting_ui_markers():
    review = FormatDiffReview(
        context_label="條目校對",
        editable_control=ft.TextField(value="目前譯文"),
    )
    result = review.render("A §7 line", "目前譯文")

    assert result.legacy_messages == ("少了 1 個「§7」",)
    assert review._result is result
    assert review._source_text == "A §7 line"
    assert review._target_text == "目前譯文"
    assert all(
        "缺少" not in (span.text or "")
        for span in review._text_panel("目前譯文", "目前譯文", (), set(), "em")
        .content.controls[-1]
        .spans
    )

    review._select_tab("edit")
    assert review._tab == "edit"
    assert review.control.content.controls


def test_review_tab_switch_pushes_content_to_the_page_immediately():
    """切「差異預覽／編輯譯文」要立刻更新內容容器，不能等下一次整頁更新。"""
    import flet as ft

    from app.views.moddb.format_diff_review import FormatDiffReview

    review = FormatDiffReview(context_label="t", editable_control=ft.TextField())
    updates: list[int] = []
    review.control.update = lambda: updates.append(1)  # type: ignore[method-assign]

    review._select_tab("edit")
    review._toggle_highlight(type("E", (), {"control": ft.Switch(value=False)})())

    assert len(updates) == 2


def test_highlight_switch_is_only_shown_on_the_diff_tab():
    """編輯頁沒有可標示的內容；開關放著按了沒反應，所以只在差異預覽顯示。"""
    import flet as ft

    from app.views.moddb.format_diff_review import FormatDiffReview

    review = FormatDiffReview(context_label="t", editable_control=ft.TextField())
    review.render("§aHello %s", "你好")

    def has_switch() -> bool:
        def walk(c):
            yield c
            for child in getattr(c, "controls", None) or []:
                yield from walk(child)

        return any(c is review._highlight_switch for c in walk(review.control.content))

    assert has_switch()
    review._select_tab("edit")
    assert not has_switch()
    review._select_tab("diff")
    assert has_switch()
