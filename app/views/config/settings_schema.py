"""設定頁的版面與導覽（#134）。

設定本身（欄位、型別、預設值、說明、套用時機、是否敏感、驗證）的唯一來源是
``translation_tool/utils/config_schema.py``；這裡只放設定頁特有的「導覽頁」與「版面」，
並重新匯出 schema 的資料結構給設定頁使用。

沒有出現在 ``LAYOUT`` 的設定會自動接在所屬卡片最後面，所以新增一般設定時不需要改這個檔案。
測試（``tests/test_config_settings_coverage.py``）檢查版面只引用存在的設定且沒有重複。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from translation_tool.utils.config_schema import (  # noqa: F401 - 重新匯出給設定頁使用
    C_BATCH,
    C_BUNDLER,
    C_EXTRACTOR,
    C_KEYS,
    C_LM_BASIC,
    C_LM_FILTER,
    C_LOGGING,
    C_MERGER,
    C_MODELS,
    C_PROMPTS,
    C_SPECIES,
    C_TDB,
    C_TRANSLATOR,
    SETTINGS,
    SETTINGS_BY_PATH,
    Setting,
    editable_settings,
    get_path,
    set_path,
    ui_settings,
)

NAV_PAGES: tuple[dict[str, str], ...] = (
    {"id": "general", "label": "一般設定", "icon": "SETTINGS"},
    {"id": "api_models", "label": "API & 模型設定", "icon": "KEY"},
    {"id": "translation_behavior", "label": "翻譯行為設定", "icon": "TRANSLATE"},
    {"id": "merger", "label": "語言合併器設定", "icon": "MERGE_TYPE"},
    {"id": "prompts", "label": "提示詞管理", "icon": "MESSAGE"},
    {"id": "species_lookup", "label": "學名查詢管理", "icon": "SEARCH"},
    {"id": "batch_limits", "label": "批次與限制", "icon": "DEVELOPER_BOARD"},
    {"id": "extractor", "label": "Jar 提取設定", "icon": "FOLDER_OPEN"},
)

# ---------------------------------------------------------------------------
# 版面
# ---------------------------------------------------------------------------
# 沒有列在 LAYOUT 的設定，會自動以單欄接在所屬 page/card 的最後面。


@dataclass(frozen=True)
class F:
    """單一控制項，直接放進卡片。"""

    path: str


@dataclass(frozen=True)
class R:
    """一列多欄（欄寬依 Setting.weight）；``pad`` > 0 時右側補一個同寬的空欄。"""

    paths: tuple[str, ...]
    pad: int = 0

    def __init__(self, *paths: str, pad: int = 0):
        object.__setattr__(self, "paths", tuple(paths))
        object.__setattr__(self, "pad", pad)


@dataclass(frozen=True)
class Side:
    """固定高度、並排的多個控制項，中間加垂直分隔線。"""

    paths: tuple[str, ...]
    height: int
    spacing: int = 10

    def __init__(self, *paths: str, height: int, spacing: int = 10):
        object.__setattr__(self, "paths", tuple(paths))
        object.__setattr__(self, "height", height)
        object.__setattr__(self, "spacing", spacing)


@dataclass(frozen=True)
class Note:
    text: str


@dataclass(frozen=True)
class Head:
    text: str


@dataclass(frozen=True)
class Gap:
    height: int = 8


@dataclass(frozen=True)
class Div:
    pass


@dataclass(frozen=True)
class Labeled:
    """上方標題、中間控制項、下方說明的一欄。"""

    path: str
    title: str
    note: str = ""


@dataclass(frozen=True)
class Cols:
    """並排的多個 ``Labeled`` 欄。"""

    items: tuple[Labeled, ...]
    spacing: int = 8

    def __init__(self, *items: Labeled, spacing: int = 8):
        object.__setattr__(self, "items", tuple(items))
        object.__setattr__(self, "spacing", spacing)


@dataclass(frozen=True)
class Custom:
    """由設定頁專用元件處理的區塊（``keys`` / ``models``）。"""

    name: str


@dataclass(frozen=True)
class Card:
    title: str
    blocks: tuple[Any, ...] = field(default_factory=tuple)


def _extractor_rows() -> tuple[Any, ...]:
    return (
        R(
            "extractor.output_folder_names.lang_extract",
            "extractor.output_folder_names.book_extract",
            "extractor.output_folder_names.dual_extract",
        ),
        R(
            "extractor.output_folder_names.lang_preview",
            "extractor.output_folder_names.book_preview",
            "extractor.output_folder_names.dual_preview",
        ),
        Div(),
        F("extractor.skip_zh_cn_extract"),
        Note(
            "extractor.target_language 是歷史設定；目前沒有正式生效語意，"
            "保留舊 config 讀取相容但不再宣稱會影響提取。"
        ),
    )


# page id → 該頁卡片的版面（只需列出有特殊排版的卡片；其餘卡片由 SETTINGS 自動產生）
LAYOUT: dict[str, tuple[Card, ...]] = {
    "general": (
        Card(C_LOGGING),
        Card(
            C_TRANSLATOR,
            (
                F("translator.output_dir_name"),
                F("ftb_translator.output_dir_name"),
                F("translator.replace_rules_path"),
                F("translator.cache_directory"),
                F("translator.parallel_execution_workers"),
                F("translator.enable_cache_saving"),
                F("translator.custom_translator_folder"),
                Note(
                    "translator.cjk_ratio_threshold 已保留供舊設定相容，歷史上沒有實際 caller；"
                    "本頁不提供無效的可調整欄位。"
                ),
            ),
        ),
        Card(C_TDB),
        Card(C_BUNDLER),
    ),
    "api_models": (
        Card(C_KEYS, (Custom("keys"),)),
        Card(C_MODELS, (Custom("models"),)),
    ),
    "translation_behavior": (
        Card(
            C_LM_BASIC,
            (
                R(
                    "lm_translator.temperature",
                    "lm_translator.rate_limit.timeout",
                    "lm_translator.rate_limit.sleep_seconds_between_batches",
                    "lm_translator.lm_translate_folder_name",
                ),
            ),
        ),
        Card(
            C_LM_FILTER,
            (
                Side(
                    "lm_translator.translator.skip_terms",
                    "lm_translator.translator.translatable_keywords",
                    "lm_translator.patchouli.dir_names",
                    height=200,
                    spacing=5,
                ),
                R("lm_translator.translator.short_text_skip_len", pad=2),
            ),
        ),
    ),
    "prompts": (
        Card(
            C_PROMPTS,
            (
                Side(
                    "lm_translator.patchouli_system_prompt",
                    "lm_translator.lang_system_prompt",
                    height=250,
                ),
            ),
        ),
    ),
    "species_lookup": (Card(C_SPECIES),),
    "batch_limits": (
        Card(
            C_BATCH,
            (
                R(
                    "lm_translator.initial_batch_size_patchouli",
                    "lm_translator.initial_batch_size_lang",
                    "lm_translator.initial_batch_size_ftb",
                ),
                R(
                    "lm_translator.initial_batch_size_kubejs",
                    "lm_translator.initial_batch_size_md",
                    "lm_translator.min_batch_size",
                    "lm_translator.batch_shrink_factor",
                ),
                R(
                    "lm_translator.rpm_cooldown_sec",
                    "lm_translator.max_output_tokens",
                    "lm_translator.key_failure_cooldown_sec",
                ),
                R(
                    "lm_translator.max_output_token_budget",
                    "lm_translator.max_input_token_budget",
                ),
                R(
                    "lm_translator.token_budget_enabled",
                    "lm_translator.output_token_factor",
                    "lm_translator.budget_min_scale",
                ),
                R(
                    "lm_translator.budget_recover_after",
                    "lm_translator.budget_recover_factor",
                    "lm_translator.batch_write_interval",
                ),
            ),
        ),
    ),
    "merger": (
        Card(
            C_MERGER,
            (
                R(
                    "lang_merger.pending_folder_name",
                    "lang_merger.pending_organized_folder_name",
                ),
                R(
                    "lang_merger.filtered_pending_min_count",
                    "lang_merger.quarantine_folder_name",
                ),
                Gap(),
                Head("語系過濾設定"),
                Cols(Labeled("lang_merger.zh_en_letter_threshold", "zh 英文含量閾值")),
                Gap(),
                Head("Patchouli 進階設定"),
                Cols(
                    Labeled(
                        "lang_merger.patchouli_skip_en_us_when_zh_cn_exists",
                        "翻譯來源優先級：繁中 > 簡中(達門檻) > 英文",
                    ),
                    Labeled(
                        "lang_merger.patchouli_effective_translation_threshold",
                        "en_us 跳過門檻",
                        "內容中日韓文字佔比達此值時視為有效翻譯",
                    ),
                ),
                Gap(),
                Head("檔案合併(階段 2)"),
                Cols(
                    Labeled(
                        "lang_merger.enable_extracted_to_assets_merge",
                        "合併 XX_extracted → assets/",
                        "merge 後跑階段 2,把 {XX_extracted}/{modid}/lang/* 補入 assets/{modid}/lang/*",
                    )
                ),
            ),
        ),
    ),
    "extractor": (Card(C_EXTRACTOR, _extractor_rows()),),
}

# 儲存前驗證／正規化（名稱對應 Setting.validator）。實作在 config_actions，避免這裡依賴服務層。
VALIDATOR_NAMES = frozenset({"log_format"})


# ---------------------------------------------------------------------------
# 工具函式
# ---------------------------------------------------------------------------


def resolved_layout() -> dict[str, tuple[Card, ...]]:
    """把 LAYOUT 與 SETTINGS 合併：未被版面引用的設定，自動接在所屬卡片最後面。

    回傳 page id → 卡片清單（保持 NAV_PAGES 的頁面順序；卡片順序為 LAYOUT 先、
    新增的卡片依 SETTINGS 出現順序）。
    """
    placed: set[str] = set()
    for cards in LAYOUT.values():
        for card in cards:
            for block in card.blocks:
                placed.update(_block_paths(block))

    result: dict[str, list[Card]] = {
        page["id"]: list(LAYOUT.get(page["id"], ())) for page in NAV_PAGES
    }
    extra: dict[tuple[str, str], list[Any]] = {}
    for s in editable_settings():
        if s.path in placed or s.page is None or s.card is None:
            continue
        extra.setdefault((s.page, s.card), []).append(F(s.path))
    for (page, title), blocks in extra.items():
        cards = result.setdefault(page, [])
        for i, card in enumerate(cards):
            if card.title == title:
                cards[i] = Card(card.title, (*card.blocks, *blocks))
                break
        else:
            cards.append(Card(title, tuple(blocks)))
    # 只有標題沒有內容的卡片（例如 Card(C_LOGGING)）已在上面補齊；
    return {page: tuple(cards) for page, cards in result.items()}


def _block_paths(block: Any) -> list[str]:
    if isinstance(block, F):
        return [block.path]
    if isinstance(block, (R, Side)):
        return list(block.paths)
    if isinstance(block, Cols):
        return [item.path for item in block.items]
    return []


def layout_paths(layout: dict[str, tuple[Card, ...]]) -> list[str]:
    paths: list[str] = []
    for cards in layout.values():
        for card in cards:
            for block in card.blocks:
                paths.extend(_block_paths(block))
    return paths
