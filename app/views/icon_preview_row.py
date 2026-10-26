"""IconPreviewView 使用的單筆語系列 UI 元件。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import unicodedata
from collections.abc import Callable
from pathlib import Path

import flet as ft
from PIL import Image as PILImage

from app.icon_preview_image import describe_icon_uri, render_icon_uri
from app.icon_render import (
    DISPLAY_PX,
    SYNTHETIC_SCHEME,
    render_flat,
    render_placeholder,
)
from app.services_impl.icon_service import (
    IconResult,
    IconRisk,
    generate_icon_preview,
    resolve_icon_with_reason,
)
from app.ui import kit
from app.ui.design import C
from app.views.icon_preview.icon_cache import _key_needs_icon


def _ensure_icon_size(src_path: Path) -> Path:
    """把磁碟上的舊式預覽圖轉成 ``DISPLAY_PX`` 見方、整數倍最近鄰放大的 PNG 並回傳路徑。

    已是標準預覽（尺寸正確）時直接回傳；任何錯誤都回傳原路徑，不阻斷顯示。
    """
    try:
        with PILImage.open(src_path) as img:
            if img.size == (DISPLAY_PX, DISPLAY_PX):
                return src_path
        out_path = src_path.parent / f"{src_path.stem}_{DISPLAY_PX}{src_path.suffix}"
        if not out_path.exists():
            out_path.write_bytes(render_flat(src_path.read_bytes()))
        return out_path
    except Exception:  # noqa: BLE001
        # 任何錯誤（讀不到、不是有效圖片等）都回傳原路徑，不阻斷顯示
        return src_path


def to_halfwidth(text):
    """將字串轉換為半形。"""
    if not isinstance(text, str):
        return text
    return unicodedata.normalize("NFKC", text)


def _resolve_preview_path(icon_result, preview_root: Path) -> Path | None:
    """舊式磁碟路徑的預覽圖；``jar://`` 與程式繪製的 URI 由 ``render_icon_uri`` 處理。"""
    icon_path = icon_result.icon_path
    if not icon_path or _is_uri_icon(icon_path):
        return None
    return generate_icon_preview(icon_path, preview_root)


def _is_uri_icon(icon_path) -> bool:
    text = str(icon_path)
    return text.startswith(("jar://", SYNTHETIC_SCHEME))


def prepare_row_icon(
    lang_key: str, assets_root: Path, preview_root: Path, icon_path: str | None
):
    """準備一列的圖示（**會做磁碟／ZIP／圖片 I/O**，必須在背景執行緒呼叫，#114）。

    回傳 ``(icon_result, image_bytes)``：圖片已渲染成 ``DISPLAY_PX`` 見方的 PNG bytes
    （取動畫首格、整數倍最近鄰放大、方塊為等角預覽），交給 ``LangItemRow(prepared_icon=...)``
    後建構列不再做任何 I/O。Flet Web 無法直接載入伺服器上的任意絕對路徑，因此傳 bytes 給
    ``ft.Image``。
    """
    if icon_path:
        # jar:// URI 不能轉 Path（Windows 上 Path("jar://...") 會變成 jar:\C:\... 格式）
        icon_result = IconResult(icon_path=icon_path, reason="", risk=None)
    else:
        icon_result = resolve_icon_with_reason(lang_key, assets_root)
    if icon_result.icon_path and _is_uri_icon(icon_result.icon_path):
        return icon_result, render_icon_uri(str(icon_result.icon_path))
    preview_path = _resolve_preview_path(icon_result, preview_root)
    if preview_path:
        preview_path = _ensure_icon_size(preview_path)
        try:
            return icon_result, preview_path.read_bytes()
        except OSError:
            return icon_result, None
    return icon_result, None


_placeholder_png: bytes | None = None


def _placeholder_bytes() -> bytes:
    """所有「找不到圖示」列共用同一張佔位圖（只繪製一次）。"""
    global _placeholder_png
    if _placeholder_png is None:
        _placeholder_png = render_placeholder()
    return _placeholder_png


def _image_control(src: bytes, tooltip: str) -> ft.Image:
    """像素圖以 1:1 顯示並關閉插值，HiDPI 放大時仍維持銳利邊緣。"""
    return ft.Image(
        src=src,
        width=DISPLAY_PX,
        height=DISPLAY_PX,
        fit=ft.BoxFit.CONTAIN,
        filter_quality=ft.FilterQuality.NONE,
        gapless_playback=True,
        tooltip=tooltip,
    )


def _build_icon_widget(
    image_bytes: bytes | None, icon_result, lang_key: str = ""
) -> tuple[ft.Control | None, ft.Text | None]:
    """建立圖示控制項。回傳 (icon_widget, risk_label)。

    有圖示：顯示預覽並在懸停時列出貼圖／模型／來源。內容類 key（item/block 等）找不到圖示：
    顯示一致的佔位圖與原因；非內容類 key（tooltip、訊息等）不佔版面。
    """
    if image_bytes:
        return _image_control(
            image_bytes, describe_icon_uri(icon_result.icon_path)
        ), None
    risk_label = None
    if icon_result.reason:
        color_map = {
            IconRisk.IGNORE: C.EM,
            IconRisk.WARN: C.GOLD,
            IconRisk.DANGER: C.RED,
        }
        risk_label = ft.Text(
            f"⚠ {icon_result.reason}",
            size=12,
            color=color_map.get(icon_result.risk, C.MUTED),
        )
    if not (lang_key and _key_needs_icon(lang_key)):
        return None, risk_label
    return _image_control(_placeholder_bytes(), describe_icon_uri(None)), risk_label


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
        prepared_icon=None,
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
            prepared_icon: ``prepare_row_icon`` 在背景執行緒算好的 ``(icon_result, image_bytes)``；
                       提供時建構列不做任何 I/O（event loop 上使用）。未提供則同步計算（相容舊呼叫）。
        """
        super().__init__(
            padding=ft.Padding.symmetric(vertical=12, horizontal=12),
            border_radius=12,
            bgcolor=C.PANEL,
            border=ft.Border.all(1, C.LINE),
        )

        self.lang_key = lang_key
        self.on_value_changed = on_value_changed

        # =========================
        # 🖼 Icon + 分類
        # =========================
        if prepared_icon is not None:
            icon_result, image_bytes = prepared_icon
            icon_widget, risk_label = _build_icon_widget(
                image_bytes, icon_result, lang_key
            )
        else:
            icon_result, image_bytes = prepare_row_icon(
                lang_key, assets_root, preview_root, icon_path
            )
            icon_widget, risk_label = _build_icon_widget(
                image_bytes, icon_result, lang_key
            )

        # =========================
        # 📝 文字區
        # =========================
        text_col = self._build_text_column(zh_text, lang_key, en_text, risk_label)

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

    def _build_text_column(self, zh_text, lang_key, en_text, risk_label) -> ft.Column:
        """翻譯、lang key、英文原文欄位組成的文字區。"""
        return ft.Column(
            spacing=6,
            expand=True,
            controls=[
                # 繁中翻譯（可編輯）
                kit.field(
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
                kit.field(
                    value=to_halfwidth(lang_key),
                    label="lang key:",
                    read_only=True,
                    border=ft.NoInputBorder(),
                    text_size=12,
                ),
                # 英文原文（可選取）
                kit.field(
                    value=to_halfwidth(en_text),
                    label="英文原文:",
                    read_only=True,
                    multiline=True,
                    border=ft.NoInputBorder(),
                    text_size=14,
                ),
                risk_label if risk_label else ft.Container(),
            ],
        )
