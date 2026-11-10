"""條目編輯區的原文標題依實際來源語系顯示。"""

from __future__ import annotations

from app.views.moddb.format_diff_review import (
    ENGLISH_SOURCE_LABEL,
    FormatDiffReview,
    source_text_label,
)
from translation_tool.translation_db.models import ScanItem
from translation_tool.translation_db.repository import TranslationDB


def test_label_follows_the_source_locale():
    assert source_text_label("en_us", has_origin=True) == ENGLISH_SOURCE_LABEL
    assert source_text_label("pt_br", has_origin=True) == "原文・pt_br"
    # jar 掃描不記來源，原文一定是 en_us
    assert source_text_label(None, has_origin=False) == ENGLISH_SOURCE_LABEL
    # 有來源紀錄卻還原不出語系：不推斷成英文
    assert source_text_label(None, has_origin=True) == "原文・語系未知"


def _pack_item(key: str, text: str, locale: str) -> ScanItem:
    return ScanItem(
        kind="kubejs_lang",
        mod_id="demo@Pack@000000000000",
        key=key,
        en_us=text,
        source_path=f"assets/demo/lang/{locale}.json",
        source_selector=f"lang:{locale}:{key}",
        source_hash="H",
        pack_scope="Pack@000000000000",
        source_locale=locale,
    )


def test_entry_detail_reports_the_source_locale(tmp_path):
    db = TranslationDB(tmp_path / "t.db")
    db.ingest(
        "1.21.1",
        [
            _pack_item("a", "Mostre sua Cara", "pt_br"),
            _pack_item("b", "Show your face", "en_us"),
            ScanItem(kind="lang", mod_id="demo", key="c", en_us="From a jar"),
        ],
    )
    locales = {}
    for entry_id, key in db._q("SELECT id, key FROM entry"):
        detail = db.entry_detail(entry_id)
        locales[key] = (detail.source_locale, bool(detail.origins))
    assert locales == {
        "a": ("pt_br", True),
        "b": ("en_us", True),
        "c": (None, False),
    }


def test_review_panel_uses_the_label_passed_to_render():
    review = FormatDiffReview(context_label="條目校對")
    assert review.source_label == ENGLISH_SOURCE_LABEL
    review.render("Mostre sua Cara", "", source_label="原文・pt_br")
    assert review.source_label == "原文・pt_br"
    # 之後的重繪（例如輸入譯文）沒有再傳標題時沿用同一個
    review.render("Mostre sua Cara", "露出你的臉")
    assert review.source_label == "原文・pt_br"
