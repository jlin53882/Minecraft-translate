"""條目編輯區的原文標題依實際來源語系顯示。"""

from __future__ import annotations

from app.views.moddb.format_diff_review import FormatDiffReview
from app.views.moddb.source_text_label import (
    ENGLISH_SOURCE_LABEL,
    source_text_label,
    source_text_name,
)
from translation_tool.translation_db.models import ScanItem
from translation_tool.translation_db.repository import TranslationDB


def test_label_follows_the_source_locale():
    assert source_text_label("kubejs_lang", "en_us") == ENGLISH_SOURCE_LABEL
    assert source_text_label("kubejs_lang", "pt_br") == "原文・pt_br"
    assert source_text_name("ftb_quest_lang", "ru_ru") == "原文（ru_ru）"
    assert source_text_name("lang", "en_us") == "英文原文"


def test_unknown_locale_is_english_only_for_kinds_without_locale_files():
    # jar 內容、KubeJS 腳本字串、FTB 任務結構沒有記語系：原文是英文
    for kind in ("lang", "patchouli", "kubejs_script", "ftb_quests"):
        assert source_text_label(kind, None) == ENGLISH_SOURCE_LABEL
    # 語系由語系檔決定的種類還原不出語系：不推斷成英文
    for kind in ("kubejs_lang", "ftb_quest_lang"):
        assert source_text_label(kind, None) == "原文・語系未知"
        assert source_text_name(kind, "") == "原文（語系未知）"


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
