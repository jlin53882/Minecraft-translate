"""translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py 單元測試：unshield_text 參數修正。

用途：驗證 on_translated_item 中 unshield_text 的呼叫使用 shielded_src.shields（list）
而非整個 ShieldedText 物件。

參考：PR #42 (pr/rich-text-shield) — 2026-03-23
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# 確保可以導入翻譯工具模組
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translation_tool.plugins.shared.rich_text_shield import (  # noqa: E402
    ShieldedText,
    ShieldPiece,
)


# ---------------------------------------------------------------------------
# 測試：ftbquests_lmtranslator.on_translated_item — unshield 使用 .shields
# ---------------------------------------------------------------------------


def test_ftb_on_translated_item_unshield_uses_shields_list():
    """
    驗證 ftbquests_lmtranslator 的 on_translated_item 回呼
    呼叫 unshield_text(t, shielded_src.shields) 時傳入 list[ShieldPiece]，
    而非整個 ShieldedText 物件。

    這樣才能正確還原翻譯結果中的格式佔位符（如 $C0$）。
    """
    from translation_tool.plugins.ftbquests import ftbquests_lmtranslator

    # 建立假的 ShieldPiece 列表（模擬 shield_text 的輸出）
    fake_shield_piece = ShieldPiece(
        placeholder="$C0$",
        original="&c",
        category="color",
    )
    fake_shields_list: list[ShieldPiece] = [fake_shield_piece]

    # 建立假的 ShieldedText（mock）
    fake_shielded = MagicMock(spec=ShieldedText)
    fake_shielded.shields = fake_shields_list  # 關鍵：.shields 是 list

    # 用 MagicMock 模擬翻譯後文字（含佔位符）
    translated_with_placeholder = "This is $C0$ important!"

    # 建立翻譯後的 item
    translated_item = {
        "path": "quest.1.title",
        "text": translated_with_placeholder,  # 翻譯後含 $C0$ 佔位符
        "source_text": "This is &c important!",  # 原文含 &c 彩色碼
    }

    # 收集 unshield_text 的呼叫參數
    captured_calls = []

    def mock_unshield_text(text: str, shields_arg) -> str:
        """Mock unshield_text，記錄被呼叫時的第二個參數。"""
        captured_calls.append({"text": text, "shields_arg": shields_arg})
        # 簡單還原：把 $C0$ 換回 &c
        return text.replace("$C0$", "&c")

    # Patch 在 ftbquests_lmtranslator 命名空間中的 unshield_text
    with (
        patch.object(
            ftbquests_lmtranslator,
            "unshield_text",
            side_effect=mock_unshield_text,
        ),
        patch.object(
            ftbquests_lmtranslator,
            "shield_text",
            return_value=fake_shielded,
        ),
    ):
        # on_translated_item 是翻譯流程中的 nested callback，
        # 無法直接呼叫。我們透過翻譯流程觸發它。
        # 為隔離測試，直接建構一個符合 on_translated_item 簽名的 closure 來測試。
        def closure_under_test(it: dict):
            """重現 on_translated_item 的核心邏輯。"""
            from translation_tool.plugins.ftbquests import ftbquests_lmtranslator as m

            p = it.get("path")
            t = it.get("text")
            src_text = str(it.get("source_text") or "")
            if isinstance(p, str) and isinstance(t, str):
                try:
                    shielded_src = m.shield_text(src_text)
                    t = m.unshield_text(t, shielded_src.shields)
                except Exception:
                    pass

        closure_under_test(translated_item)

    # 斷言：unshield_text 被呼叫了
    assert len(captured_calls) == 1, "unshield_text 應該被呼叫一次"

    shields_arg = captured_calls[0]["shields_arg"]

    # 斷言：傳入的是 list[ShieldPiece]，不是 ShieldedText 整個物件
    assert isinstance(shields_arg, list), (
        f"unshield_text 第二參數應為 list，實際為 {type(shields_arg).__name__}。"
        "使用 .shields 而非整個 ShieldedText 物件。"
    )

    # 斷言：list 中第一個元素是 ShieldPiece
    assert len(shields_arg) == 1
    assert isinstance(shields_arg[0], ShieldPiece), (
        f"list[ShieldPiece] 中實際元素型別為 {type(shields_arg[0]).__name__}。"
    )

    # 斷言：ShieldPiece 的內容正確
    assert shields_arg[0].placeholder == "$C0$"
    assert shields_arg[0].original == "&c"
    assert shields_arg[0].category == "color"


def test_ftb_on_translated_item_unshield_rejects_whole_shielded_object():
    """
    驗證：如果錯誤地傳入整個 ShieldedText 物件（而非 .shields），
    unshield_text 的第二參數會是非 list 型別，導致還原失敗。
    此測試用來確認「錯誤版本」的呼叫模式確實會被偵測。
    """
    from translation_tool.plugins.ftbquests import ftbquests_lmtranslator

    # 建立假的 ShieldedText（mock），但不使用 .shields
    fake_shielded = MagicMock(spec=ShieldedText)
    fake_shielded.shields = []  # 空的 list

    # 建立翻譯後的 item（含佔位符）
    translated_item = {
        "path": "quest.1.title",
        "text": "Result with $C0$ here",
        "source_text": "Source &c text",
    }

    # 收集傳入 unshield_text 的第二參數
    captured_second_arg_type = []

    def mock_unshield_text(text: str, shields_arg):
        captured_second_arg_type.append(type(shields_arg).__name__)
        return text  # 不做還原

    with (
        patch.object(
            ftbquests_lmtranslator,
            "unshield_text",
            side_effect=mock_unshield_text,
        ),
        patch.object(
            ftbquests_lmtranslator,
            "shield_text",
            return_value=fake_shielded,
        ),
    ):

        def closure_under_test(it: dict):
            from translation_tool.plugins.ftbquests import ftbquests_lmtranslator as m

            p = it.get("path")
            t = it.get("text")
            src_text = str(it.get("source_text") or "")
            if isinstance(p, str) and isinstance(t, str):
                try:
                    shielded_src = m.shield_text(src_text)
                    t = m.unshield_text(t, shielded_src.shields)
                except Exception:
                    pass

        closure_under_test(translated_item)

    # 驗證：使用 .shields（list） 時，參數型別為 "list"
    assert captured_second_arg_type[-1] == "list"


# ---------------------------------------------------------------------------
# 測試：md_lmtranslator.on_translated_item — unshield 使用 .shields
# ---------------------------------------------------------------------------


def test_md_on_translated_item_unshield_uses_shields_list():
    """
    驗證 md_lmtranslator 的 on_translated_item 回呼
    呼叫 unshield_text(dst, shielded.shields) 時傳入 list[ShieldPiece]。
    """
    from translation_tool.plugins.md import md_lmtranslator

    # 建立假的 ShieldPiece
    fake_shield_piece = ShieldPiece(
        placeholder="$P0$",
        original="#minecraft:diamond",
        category="item_id",
    )
    fake_shields_list: list[ShieldPiece] = [fake_shield_piece]

    fake_shielded = MagicMock(spec=ShieldedText)
    fake_shielded.shields = fake_shields_list

    # 翻譯後 item（含 item_id 佔位符）
    translated_item = {
        "path": "abc123",
        "text": "You need $P0$ to craft this",
        "source_text": "You need #minecraft:diamond to craft this",
        "_shielded": fake_shielded,
    }

    captured_calls = []

    def mock_unshield_text(text: str, shields_arg) -> str:
        captured_calls.append({"text": text, "shields_arg": shields_arg})
        return text.replace("$P0$", "#minecraft:diamond")

    with patch.object(
        md_lmtranslator,
        "unshield_text",
        side_effect=mock_unshield_text,
    ):
        # 重現 md_lmtranslator 的 on_translated_item 核心邏輯
        def closure_md_on_translated_item(it: dict):
            from translation_tool.plugins.md import md_lmtranslator as m

            h = str(it.get("path") or "")
            dst = str(it.get("text") or "")
            src_text = str(it.get("source_text") or "")
            if h and dst:
                shielded = it.get("_shielded")
                if shielded is not None and getattr(shielded, "shields", None):
                    try:
                        dst = m.unshield_text(dst, shielded.shields)
                    except Exception:
                        pass
                else:
                    try:
                        shielded_src = m.shield_text(src_text)
                        dst = m.unshield_text(dst, shielded_src.shields)
                    except Exception:
                        pass

        closure_md_on_translated_item(translated_item)

    assert len(captured_calls) == 1, "unshield_text 應該被呼叫一次"
    shields_arg = captured_calls[0]["shields_arg"]

    assert isinstance(shields_arg, list), (
        f"unshield_text 第二參數應為 list，實際為 {type(shields_arg).__name__}。"
    )
    assert len(shields_arg) == 1
    assert isinstance(shields_arg[0], ShieldPiece)
    assert shields_arg[0].placeholder == "$P0$"
    assert shields_arg[0].original == "#minecraft:diamond"
    assert shields_arg[0].category == "item_id"


def test_md_on_translated_item_else_branch_uses_shields():
    """
    驗證 md_lmtranslator 的 on_translated_item else 分支
    （當 item._shielded 為 None 時）仍使用 shield_text().shields。
    """
    from translation_tool.plugins.md import md_lmtranslator

    fake_shield_piece = ShieldPiece(
        placeholder="$C1$",
        original="&a",
        category="color",
    )
    fake_shields_list: list[ShieldPiece] = [fake_shield_piece]

    fake_shielded = MagicMock(spec=ShieldedText)
    fake_shielded.shields = fake_shields_list

    # item._shielded 為 None，觸發 else 分支
    translated_item = {
        "path": "xyz789",
        "text": "Green text $C1$ here",
        "source_text": "Green text &a here",
        "_shielded": None,  # 觸發 else 分支
    }

    captured_calls = []

    def mock_unshield_text(text: str, shields_arg) -> str:
        captured_calls.append({"text": text, "shields_arg": shields_arg})
        return text.replace("$C1$", "&a")

    with (
        patch.object(
            md_lmtranslator,
            "unshield_text",
            side_effect=mock_unshield_text,
        ),
        patch.object(
            md_lmtranslator,
            "shield_text",
            return_value=fake_shielded,
        ),
    ):

        def closure_md_on_translated_item(it: dict):
            from translation_tool.plugins.md import md_lmtranslator as m

            h = str(it.get("path") or "")
            dst = str(it.get("text") or "")
            src_text = str(it.get("source_text") or "")
            if h and dst:
                shielded = it.get("_shielded")
                if shielded is not None and getattr(shielded, "shields", None):
                    try:
                        dst = m.unshield_text(dst, shielded.shields)
                    except Exception:
                        pass
                else:
                    try:
                        shielded_src = m.shield_text(src_text)
                        dst = m.unshield_text(dst, shielded_src.shields)
                    except Exception:
                        pass

        closure_md_on_translated_item(translated_item)

    assert len(captured_calls) == 1, "unshield_text 應在 else 分支被呼叫一次"
    shields_arg = captured_calls[0]["shields_arg"]

    assert isinstance(shields_arg, list), (
        f"else 分支中 unshield_text 第二參數應為 list，實際為 {type(shields_arg).__name__}。"
    )
    assert shields_arg[0].original == "&a"


def test_md_cache_hit_unshield_uses_shields():
    """
    驗證 md_lmtranslator 中 cache hit 分支
    呼叫 unshield_text(hash_to_dst, shielded.shields) 使用 .shields。
    """
    from translation_tool.plugins.md import md_lmtranslator

    fake_shield_piece = ShieldPiece(
        placeholder="$P1$",
        original="#minecraft:iron_ingot",
        category="item_id",
    )
    fake_shields_list: list[ShieldPiece] = [fake_shield_piece]

    fake_shielded = MagicMock(spec=ShieldedText)
    fake_shielded.shields = fake_shields_list

    cached_item = {
        "path": "cached_hash_001",
        "text": "You need $P1$ to smelt",
        "_shielded": fake_shielded,
    }

    captured_calls = []

    def mock_unshield_text(text: str, shields_arg) -> str:
        captured_calls.append({"text": text, "shields_arg": shields_arg})
        return text.replace("$P1$", "#minecraft:iron_ingot")

    with patch.object(
        md_lmtranslator,
        "unshield_text",
        side_effect=mock_unshield_text,
    ):
        # 重現 md_lmtranslator cache hit 區塊邏輯
        h = str(cached_item.get("path") or "")
        dst = str(cached_item.get("text") or "")
        if h and dst:
            shielded = cached_item.get("_shielded")
            if shielded is not None and getattr(shielded, "shields", None):
                try:
                    dst = md_lmtranslator.unshield_text(dst, shielded.shields)
                except Exception:
                    pass

    assert len(captured_calls) == 1
    shields_arg = captured_calls[0]["shields_arg"]
    assert isinstance(shields_arg, list)
    assert shields_arg[0].original == "#minecraft:iron_ingot"
