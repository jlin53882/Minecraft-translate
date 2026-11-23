"""手冊／指南資料夾名稱的匹配規則。

設定 ``lm_translator.patchouli.dir_names`` 裡的名稱是「資料夾名稱」（相對名稱，不是路徑）：
``assets/<模組>/`` 底下任何深度、名稱相同的資料夾都算。例如設定 ``guide`` 可以對到
``assets/little_big_redstone/guides/little_big_redstone/guide/``，設定 ``ae2guide`` 可以對到
``assets/ae2/ae2guide/``。名稱比對不分大小寫。
"""

from __future__ import annotations

from collections.abc import Sequence

DEFAULT_BOOK_DIRS: tuple[str, ...] = (
    "patchouli_books",
    "book",
    "manual",
    "guidebook",
)

GUIDE_DIR_NAMES = frozenset(
    {
        "ae2guide",
        "guide",
        "guides",
        "guidebook",
        "manual",
        "manuals",
        "documentation",
        "docs",
    }
)


def is_guide_directory_segment(segment: str) -> bool:
    """路徑片段是不是指南／文件資料夾（``ae2guide``、``guides``、``<名稱>guide``）。"""

    lowered = segment.casefold()
    return lowered in GUIDE_DIR_NAMES or lowered.endswith(("guide", "guidebook"))


# ``assets/<模組>/`` 之後才開始比對資料夾名稱，所以索引從 2 起算
FIRST_DIR_INDEX = 2


def find_book_dir_index(parts: Sequence[str], dir_names: Sequence[str]) -> int | None:
    """``parts``（以 ``assets``／``data`` 開頭的路徑片段）中第一個符合名稱的資料夾索引。

    只比對模組 id 之後、檔名之前的資料夾；沒有符合回傳 None。
    """
    wanted = {name.strip().lower() for name in dir_names if str(name).strip()}
    for index in range(FIRST_DIR_INDEX, len(parts) - 1):
        if parts[index].lower() in wanted:
            return index
    return None
