from pathlib import Path
from PIL import Image, UnidentifiedImageError
import hashlib


def generate_icon_preview(icon_path: Path, preview_root: Path) -> Path | None:
    """
    生成 icon 預覽圖（安全版）

    - 成功 → 回傳 preview png 路徑
    - 失敗 → 回傳 None（不中斷 UI）
    """

    if not icon_path.exists():
        return None

    try:
        # 以檔案內容 hash 作為快取 key
        data = icon_path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()[:16]
        preview_root.mkdir(parents=True, exist_ok=True)
        preview_path = preview_root / f"{digest}.png"

        if preview_path.exists():
            return preview_path

        # 嘗試開啟圖片
        img = Image.open(icon_path)
        img = img.convert("RGBA")

        # Minecraft icon 通常 16x16，放大用 NEAREST
        img = img.resize((64, 64), Image.NEAREST)

        img.save(preview_path)
        return preview_path

    except (UnidentifiedImageError, OSError, ValueError) as e:
        # ❗ 關鍵：任何圖片錯誤都「吞掉」
        print(f"[WARN] 無法產生 icon 預覽：{icon_path} → {e}")
        return None
