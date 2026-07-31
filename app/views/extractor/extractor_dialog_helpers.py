"""extractor_dialog 輔助函式。"""


def format_size(size_mb: float) -> str:
    """格式化大小顯示:大於 1 MB 顯示 MB,小於 1 MB 顯示 KB,更小顯示 B。

    🐛 2026-07-14 user review:預覽結果裡小檔案(< 1 MB)用 r['size_mb']:.1f
    顯示成 0.0 MB,user 看不到實際大小。改用此 helper 自動選擇合適單位。

    Args:
        size_mb: 大小(MB)

    Returns:
        格式化字串,例如 "5.5 MB"、"30.0 KB"、"105 B"
    """
    if size_mb >= 1.0:
        return f"{size_mb:.1f} MB"
    elif size_mb >= 0.001:
        kb = size_mb * 1024
        return f"{kb:.1f} KB"
    else:
        bytes_size = size_mb * 1024 * 1024
        return f"{bytes_size:.0f} B"
