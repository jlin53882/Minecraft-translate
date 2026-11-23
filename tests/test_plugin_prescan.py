"""預掃描共用預覽欄位清理。"""

from __future__ import annotations

from translation_tool.core.lm_translator_shared_preview import strip_runtime_fields


def test_strip_runtime_fields_drops_shield_without_mutating_input():
    items = [{"path": "a", "text": "x", "_shielded": object()}, {"path": "b"}]

    cleaned = strip_runtime_fields(items)

    assert cleaned == [{"path": "a", "text": "x"}, {"path": "b"}]
    assert "_shielded" in items[0]  # 原 item 仍保留，翻譯流程還要用
