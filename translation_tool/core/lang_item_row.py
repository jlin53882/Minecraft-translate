"""translation_tool/core/lang_item_row.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import flet as ft
from pathlib import Path
from typing import Callable
import unicodedata
import hashlib

from PIL import Image as PILImage

from translation_tool.core.icon_preview_cache import generate_icon_preview
from translation_tool.core.icon_resolver import resolve_icon_with_reason
from translation_tool.core.icon_reason import IconRisk, IconResult

_ICON_UPSCALE_SIZE = 64
"""小於此尺寸的 icon 視為需要 upscale（pixels, 一邊）。"""


def _ensure_icon_size(src_path: Path) -> Path:
    """檢查圖片尺寸，若任一邊 < _ICON_UPSCALE_SIZE 则以 nearest neighbor 放大後寫入快取。

    用途：Minecraft JAR 內的 item icon 多為 16x16，直接放大會模糊。
          用 nearest neighbor 放大至 64x64 可保持像素風格外觀。
    """
    try:
        img = PILImage.open(src_path)
        w, h = img.size
        if w >= _ICON_UPSCALE_SIZE and h >= _ICON_UPSCALE_SIZE:
            img.close()
            return src_path

        # nearest neighbor 放大，不走抗鋸齒
        upscaled = img.resize((_ICON_UPSCALE_SIZE, _ICON_UPSCALE_SIZE), PILImage.NEAREST)
        img.close()
        # 寫入同目錄，檔名加上 _upscaled 後綴，避免覆蓋原始快取
        out_path = src_path.parent / f"{src_path.stem}_upscaled{src_path.suffix}"
        upscaled.save(out_path)
        return out_path
    except Exception:
        # 任何錯誤（讀不到、ImageFont 失敗等）都回傳原路徑，不阻斷顯示
        return src_path


# Icon reader（PR59 新增）
try:
    from app.icon_reader import IconRef, read_icon_bytes
    _HAS_ICON_READER = True
except ImportError:
    _HAS_ICON_READER = False

def to_halfwidth(text):
    """將字串轉換為半形。"""
    if not isinstance(text, str):
        return text
    return unicodedata.normalize("NFKC", text)

class LangItemRow(ft.Container):
    """LangItemRow 類別。

    用途：封裝與 LangItemRow 相關的狀態與行為。
    維護注意：修改公開方法前請確認外部呼叫點與相容性。
    """

    def __init__(
        self,
        *,
        lang_key: str,
        en_text: str,
        zh_text: str,
        assets_root: Path,
        preview_root: Path,
        on_value_changed: Callable[[str, str], None],
        icon_path: str | None = None,
    ):
        """初始化 LangItemRow。

        參數：
            lang_key: 語言 key
            en_text: 英文原文
            zh_text: 中文翻譯
            assets_root: 資源根目錄
            preview_root: 預覽根目錄
            on_value_changed: 值變更回調函數
            icon_path: 圖示路徑（可為 JAR 內的路徑 或已提取到磁碟的路徑）。
                       若有值則直接使用，跳過 resolve_icon_with_reason。
        """
        super().__init__(
            padding=ft.Padding.symmetric(vertical=10, horizontal=8),
            border_radius=8,
            bgcolor=ft.Colors.WHITE,
        )

        self.lang_key = lang_key
        self.on_value_changed = on_value_changed

        # =========================
        # 🖼 Icon + 分類
        # =========================
        # icon_path 有值（來自 JAR 掃描）：直接使用，跳過 resolve
        if icon_path:
            # jar:// URI 不能轉 Path（Windows 上 Path("jar://...") 會變成 jar:\C:\... 格式）
            # 保持 string，讓 IconRef.parse 處理
            icon_result = IconResult(
                icon_path=icon_path,  # 可能是 jar:// URI string 或磁碟 Path
                reason="",
                risk=None,
            )
        else:
            icon_result = resolve_icon_with_reason(lang_key, assets_root)
        risk_label = None

        # PR59 fix：處理 jar:// URI（新格式）與舊磁碟路徑
        if icon_result.icon_path and _HAS_ICON_READER:
            icon_ref = IconRef.parse(str(icon_result.icon_path))
            if icon_ref is not None:
                # 從 ZIP 直接讀取 bytes，寫入 preview_root
                png_bytes = read_icon_bytes(icon_ref.jar_path, icon_ref.png_path)
                if png_bytes:
                    digest = hashlib.sha256(png_bytes).hexdigest()[:16]
                    preview_root.mkdir(parents=True, exist_ok=True)
                    zip_preview_path = preview_root / f"zip_{digest}.png"
                    if not zip_preview_path.exists():
                        zip_preview_path.write_bytes(png_bytes)
                    preview_path = zip_preview_path
                else:
                    preview_path = None
            else:
                # 舊磁碟路徑（無法解析 jar://，走一般流程）
                preview_path = generate_icon_preview(icon_result.icon_path, preview_root)
        else:
            # icon_path 為 None，或無 _HAS_ICON_READER：嘗試用磁碟路徑生成預覽
            preview_path = generate_icon_preview(icon_result.icon_path, preview_root) if icon_result.icon_path else None

        # 顯示 icon 或警告
        # 修復：當 preview_path 為 None 時，不顯示任何 icon widget（佔位完全空白）
        icon_widget: ft.Control | None = None
        if preview_path:
            # 小於 32x32 的 icon（如 Minecraft 16x16 item icon）以 nearest neighbor 放大至 64x64
            upscaled_path = _ensure_icon_size(preview_path)
            icon_widget = ft.Image(
                src=str(upscaled_path),
                width=128,
                height=128,
            )
        elif icon_result.reason:
            # 無法取得 preview 且有 reason：顯示錯誤 icon + 根據 risk 等級上色
            color_map = {
                IconRisk.IGNORE: ft.Colors.GREEN_600,
                IconRisk.WARN: ft.Colors.ORANGE_600,
                IconRisk.DANGER: ft.Colors.RED_600,
            }
            icon_widget = ft.Container(
                width=128,
                height=128,
                alignment=ft.alignment.Alignment.CENTER,
                bgcolor=ft.Colors.GREY_300,
                content=ft.Icon(ft.Icons.IMAGE_NOT_SUPPORTED),
            )
            risk_label = ft.Text(
                f"⚠ {icon_result.reason}",
                size=12,
                color=color_map.get(icon_result.risk, ft.Colors.GREY_700),
            )

        # =========================
        # 📝 文字區
        # =========================
        text_col = ft.Column(
            spacing=6,
            expand=True,
            controls=[
                # 繁中翻譯（可編輯）
                ft.TextField(
                    value=to_halfwidth(zh_text or ""),
                    label="繁中翻譯:",
                    multiline=True,
                    min_lines=1,
                    max_lines=4,
                    text_size=16,
                    on_change=lambda e: self.on_value_changed(
                        self.lang_key,
                        to_halfwidth(e.control.value),
                    ),
                ),
                # lang key（可選取）
                ft.TextField(
                    value=to_halfwidth(lang_key),
                    label="lang key:",
                    read_only=True,
                    border=ft.InputBorder.NONE,
                    text_size=12,
                ),
                # 英文原文（可選取）
                ft.TextField(
                    value=to_halfwidth(en_text),
                    label="英文原文:",
                    read_only=True,
                    multiline=True,
                    border=ft.InputBorder.NONE,
                    text_size=14,
                ),
                risk_label if risk_label else ft.Container(),
            ],
        )

        # =========================
        # 🔧 最外層 Row
        # =========================
        row_controls: list[ft.Control] = []
        if icon_widget is not None:
            row_controls.append(icon_widget)
        row_controls.append(text_col)

        self.content = ft.Row(
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.START,
            controls=row_controls,
        )
