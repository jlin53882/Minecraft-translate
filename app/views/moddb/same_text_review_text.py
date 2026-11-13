"""同文一致性審核的成員分組與勾選狀態（純邏輯）。

譯文的可比對呈現（不可見字元標記、差異標示）在 ``text_compare``；這裡只保留同文審核專屬的
「成員依目前譯文分組」與「群組勾選三態」。不需要建立畫面即可測試。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MemberBucket:
    """同一種目前譯文的成員：``total`` 是整組筆數，``members`` 只含有限制內要列出的。"""

    text: str
    total: int
    members: tuple


def bucket_members(
    members, canonical_text: str | None, limit: int
) -> tuple[list[MemberBucket], int]:
    """把需要統一的成員依目前譯文分組（筆數多的在前、未翻譯放最後），最多列 ``limit`` 筆。

    回傳（分組, 因上限而未列出的筆數）。統一譯文已相同的成員不列；沒有統一譯文時全部列出。
    """
    targets = [
        m for m in members if canonical_text is None or m.zh_tw != canonical_text
    ]
    grouped: dict[str, list] = {}
    for member in targets:
        grouped.setdefault(member.zh_tw, []).append(member)
    order = sorted(grouped, key=lambda t: (not t, -len(grouped[t]), t))
    buckets: list[MemberBucket] = []
    room = limit
    for text in order:
        rows = grouped[text]
        buckets.append(MemberBucket(text, len(rows), tuple(rows[: max(room, 0)])))
        room -= len(rows)
    return buckets, max(len(targets) - limit, 0)


def default_selection() -> set[int]:
    """開啟時預設不勾選任何條目：使用者明確勾選群組或按「全選」才會統一。"""
    return set()


def selection_state(
    selectable: set[int], selected: set[int]
) -> tuple[bool | None, int]:
    """群組勾選方塊的三態與已勾選筆數：全部勾 True、全沒勾 False、部分 None；沒有可統一的為 False。"""
    chosen = len(selectable & selected)
    if not selectable or not chosen:
        return False, chosen
    return (True if chosen == len(selectable) else None), chosen
